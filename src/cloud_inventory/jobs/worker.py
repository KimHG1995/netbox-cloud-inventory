import asyncio
import inspect
import signal
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, Protocol
from uuid import UUID

from cloud_inventory.api.imports import ImportRecord, RunRecord, SourceFileRecord
from cloud_inventory.config import Settings
from cloud_inventory.domain.models import CloudResource, ResourceBatch
from cloud_inventory.ingest.artifact_store import (
    ArtifactStore,
    FileSystemArtifactStore,
)
from cloud_inventory.ingest.batch import combine_batches
from cloud_inventory.ingest.parsers.base import SourceMetadata
from cloud_inventory.ingest.parsers.registry import ParserRegistry, build_default_registry
from cloud_inventory.jobs.queue import ClaimedJob, JobQueue
from cloud_inventory.netbox.client import NetBoxClient, NetBoxObject
from cloud_inventory.netbox.writer import ApplyResult, NetBoxWriter
from cloud_inventory.persistence.models import JobStatus
from cloud_inventory.persistence.repositories import ImportRepository
from cloud_inventory.persistence.session import create_session_factory
from cloud_inventory.reconciliation.diff import PreviewResult, Reconciler

Clock = Callable[[], datetime]
CurrentLoader = Callable[
    [ResourceBatch],
    Mapping[str, CloudResource]
    | Awaitable[Mapping[str, CloudResource]],
]


class ParserLike(Protocol):
    profile_id: str
    schema_version: str

    def parse(self, path: Path, metadata: SourceMetadata) -> ResourceBatch: ...


class RegistryLike(Protocol):
    def detect(self, path: Path, metadata: SourceMetadata) -> ParserLike: ...


class WriterLike(Protocol):
    async def apply(
        self,
        preview: PreviewResult,
        checkpoint: int = 0,
    ) -> ApplyResult: ...


class WorkerRepository(Protocol):
    async def get_import(self, import_id: UUID) -> ImportRecord | None: ...

    async def get_source_files(
        self,
        import_id: UUID,
    ) -> list[SourceFileRecord]: ...

    async def mark_source_parsing(self, source_id: UUID) -> None: ...

    async def set_source_parser(
        self,
        source_id: UUID,
        profile: str,
        version: str,
    ) -> None: ...

    async def save_preview(
        self,
        import_id: UUID,
        preview: PreviewResult,
        parser_versions: dict[str, str],
        expires_at: datetime,
    ) -> None: ...

    async def mark_sources_preview_ready(self, import_id: UUID) -> None: ...

    async def load_preview(
        self,
        import_id: UUID,
    ) -> tuple[PreviewResult, datetime] | None: ...

    async def get_run(self, run_id: UUID) -> RunRecord | None: ...

    async def mark_run_running(self, run_id: UUID) -> None: ...

    async def set_run_checkpoint(self, run_id: UUID, checkpoint: int) -> None: ...

    async def get_active_tag_mappings(
        self,
        provider: str,
        realm: str,
        account_id: str,
    ) -> Sequence[Any]: ...

    async def get_active_owner_mappings(
        self,
        provider: str,
        realm: str,
        account_id: str,
    ) -> Sequence[Any]: ...

    async def finish_run(self, run_id: UUID, result: ApplyResult) -> None: ...

    async def fail_run(self, run_id: UUID, error: Exception) -> None: ...

    async def mark_import_applied(self, import_id: UUID) -> None: ...

    async def list_expired_artifacts(
        self,
        now: datetime,
    ) -> list[SourceFileRecord]: ...

    async def mark_artifact_expired(self, source_id: UUID) -> None: ...


WriterFactory = Callable[..., WriterLike]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _managed_value(value: object) -> object:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _current_resource(
    desired: CloudResource,
    netbox_object: NetBoxObject,
) -> CloudResource:
    source = netbox_object.data
    custom_fields = source.get("custom_fields", {})
    managed = custom_fields if isinstance(custom_fields, dict) else source
    if not isinstance(managed, dict):
        managed = {}
    attributes = managed.get("source_attributes", source.get("source_attributes", {}))
    tags = managed.get("source_tags", source.get("source_tags", {}))
    document = desired.model_dump()
    document.update(
        {
            "name": source.get("name", desired.name),
            "status": _managed_value(
                managed.get("cloud_status", source.get("status", desired.status))
            ),
            "attributes": attributes if isinstance(attributes, dict) else {},
            "tags": tags if isinstance(tags, dict) else {},
        }
    )
    return CloudResource.model_validate(document)


class NetBoxCurrentLoader:
    def __init__(self, client: NetBoxClient) -> None:
        self._client = client

    async def __call__(
        self,
        batch: ResourceBatch,
    ) -> Mapping[str, CloudResource]:
        current: dict[str, CloudResource] = {}
        for resource in batch.resources:
            netbox_object = await self._client.get_by_cloud_uid(
                resource.resource_type,
                resource.uid,
            )
            if netbox_object is not None:
                current[resource.uid] = _current_resource(resource, netbox_object)
        return current


class InventoryWorker:
    def __init__(
        self,
        *,
        repository: WorkerRepository,
        artifact_store: ArtifactStore,
        registry: RegistryLike,
        current_loader: CurrentLoader,
        writer_factory: WriterFactory,
        clock: Clock = _utcnow,
    ) -> None:
        self._repository = repository
        self._artifact_store = artifact_store
        self._registry = registry
        self._current_loader = current_loader
        self._writer_factory = writer_factory
        self._clock = clock

    async def _parse_source(
        self,
        source: SourceFileRecord,
        metadata: SourceMetadata,
    ) -> tuple[ResourceBatch, str, str]:
        await self._repository.mark_source_parsing(source.id)
        suffix = Path(source.filename).suffix
        temporary_path: Path | None = None
        try:
            async with self._artifact_store.open(source.artifact_key) as stream:
                with tempfile.NamedTemporaryFile(
                    suffix=suffix,
                    delete=False,
                ) as temporary:
                    while chunk := stream.read(1024 * 1024):
                        temporary.write(chunk)
                    temporary_path = Path(temporary.name)
            parser = self._registry.detect(temporary_path, metadata)
            batch = parser.parse(temporary_path, metadata)
            await self._repository.set_source_parser(
                source.id,
                parser.profile_id,
                parser.schema_version,
            )
            return batch, parser.profile_id, parser.schema_version
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    async def handle_parse_import(self, job: ClaimedJob) -> None:
        import_id = UUID(str(job.payload["import_id"]))
        record = await self._repository.get_import(import_id)
        if record is None:
            raise ValueError("parse job references an unknown import")
        sources = sorted(
            await self._repository.get_source_files(import_id),
            key=lambda item: item.id,
        )
        if not sources:
            raise ValueError("import has no source files")
        metadata = SourceMetadata(
            provider=record.provider,
            realm=record.realm,
            account_id=record.account_id,
            export_type=record.export_type,
            uploaded_at=record.created_at,
            exported_at=record.exported_at,
            region=record.region,
        )
        batches: list[ResourceBatch] = []
        parser_versions: dict[str, str] = {}
        for source in sources:
            batch, profile, version = await self._parse_source(source, metadata)
            batches.append(batch)
            parser_versions[profile] = version
        combined = combine_batches(batches)
        current_result = self._current_loader(combined)
        current = (
            await current_result
            if inspect.isawaitable(current_result)
            else current_result
        )
        preview = Reconciler().preview(combined, current)
        await self._repository.save_preview(
            import_id,
            preview,
            parser_versions,
            self._clock() + timedelta(hours=24),
        )
        await self._repository.mark_sources_preview_ready(import_id)

    async def handle_apply_import(self, job: ClaimedJob) -> None:
        run_id = UUID(str(job.payload["run_id"]))
        run = await self._repository.get_run(run_id)
        if run is None:
            raise ValueError("apply job references an unknown run")
        loaded = await self._repository.load_preview(run.import_id)
        if loaded is None:
            raise ValueError("apply job has no immutable preview")
        preview, expires_at = loaded
        if preview.batch_hash != run.batch_hash:
            raise ValueError("apply Batch hash no longer matches preview")
        if expires_at <= self._clock():
            raise ValueError("apply preview has expired")

        await self._repository.mark_run_running(run_id)
        record = await self._repository.get_import(run.import_id)
        if record is None:
            raise ValueError("apply run references an unknown import")
        tag_mappings = await self._repository.get_active_tag_mappings(
            record.provider.value,
            record.realm.value,
            record.account_id,
        )
        owner_mappings = await self._repository.get_active_owner_mappings(
            record.provider.value,
            record.realm.value,
            record.account_id,
        )

        async def save_checkpoint(stage: int) -> None:
            await self._repository.set_run_checkpoint(run_id, stage)

        writer = self._writer_factory(
            tag_mappings=tag_mappings,
            owner_mappings=owner_mappings,
            checkpoint=save_checkpoint,
        )
        start_checkpoint = int(run.checkpoint or 0)
        result = await writer.apply(preview, checkpoint=start_checkpoint)
        await self._repository.finish_run(run_id, result)
        if result.failed == 0:
            await self._repository.mark_import_applied(run.import_id)

    async def expire_artifacts(self, now: datetime) -> int:
        expired = await self._repository.list_expired_artifacts(now)
        for source in expired:
            with suppress(FileNotFoundError):
                await self._artifact_store.delete(source.artifact_key)
            await self._repository.mark_artifact_expired(source.id)
        return len(expired)


async def run_worker(settings: Settings) -> NoReturn:
    engine, session_factory = create_session_factory(settings.database_url)
    repository = ImportRepository(session_factory)
    queue = JobQueue(session_factory)
    store = FileSystemArtifactStore(settings.artifact_root)
    registry: ParserRegistry = build_default_registry()
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signal_name, stop_requested.set)

    async with NetBoxClient(
        str(settings.netbox_url),
        settings.netbox_token.get_secret_value(),
    ) as netbox_client:
        worker = InventoryWorker(
            repository=repository,
            artifact_store=store,
            registry=registry,
            current_loader=NetBoxCurrentLoader(netbox_client),
            writer_factory=lambda **kwargs: NetBoxWriter(netbox_client, **kwargs),
        )
        await worker.expire_artifacts(_utcnow())
        last_expiry = _utcnow()
        try:
            while not stop_requested.is_set():
                job = await queue.claim("inventory-worker")
                if job is None:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(stop_requested.wait(), timeout=1)
                    if _utcnow() - last_expiry >= timedelta(hours=1):
                        await worker.expire_artifacts(_utcnow())
                        last_expiry = _utcnow()
                    continue
                try:
                    if job.job_type == "parse_import":
                        await worker.handle_parse_import(job)
                    elif job.job_type == "apply_import":
                        await worker.handle_apply_import(job)
                    else:
                        raise ValueError("unknown job type")
                except Exception as error:
                    failure_status = await queue.fail(job.id, error)
                    if (
                        failure_status is JobStatus.FAILED
                        and job.job_type == "apply_import"
                        and "run_id" in job.payload
                    ):
                        await repository.fail_run(
                            UUID(str(job.payload["run_id"])),
                            error,
                        )
                else:
                    await queue.succeed(job.id, {"status": "ok"})
        finally:
            await engine.dispose()
    raise SystemExit(0)


def main() -> None:
    from cloud_inventory.config import get_settings

    asyncio.run(run_worker(get_settings()))


if __name__ == "__main__":
    main()
