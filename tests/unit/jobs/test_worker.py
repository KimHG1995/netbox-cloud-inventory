from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from cloud_inventory.api.imports import ImportRecord, RunRecord, SourceFileRecord
from cloud_inventory.domain.models import (
    CloudResource,
    Completeness,
    DetailLevel,
    Provider,
    Realm,
    ResourceType,
)
from cloud_inventory.ingest.batch import finalize_batch
from cloud_inventory.ingest.parsers.base import DetectionResult, SourceMetadata
from cloud_inventory.jobs.queue import ClaimedJob
from cloud_inventory.jobs.worker import InventoryWorker
from cloud_inventory.netbox.writer import AppliedChange, ApplyResult

NOW = datetime(2026, 7, 28, 4, 0, tzinfo=UTC)


def cloud_resource(external_id: str) -> CloudResource:
    return CloudResource(
        uid=f"aws:commercial:111111111111:ap-northeast-2:virtual_machine:{external_id}",
        provider=Provider.AWS,
        realm=Realm.COMMERCIAL,
        account_id="111111111111",
        region="ap-northeast-2",
        resource_type=ResourceType.VIRTUAL_MACHINE,
        external_id=external_id,
        name=external_id,
        status="active",
        observed_at=NOW,
        completeness=Completeness.PARTIAL,
        detail_level=DetailLevel.SUMMARY,
        source_profile="fake.v1",
        source_priority=100,
    )


class FakeParser:
    profile_id = "fake.v1"
    schema_version = "7"

    def detect(self, path: Path, metadata: SourceMetadata) -> DetectionResult:
        del metadata
        return DetectionResult(path.read_bytes().startswith(b"resource="), 100, "fake")

    def parse(self, path: Path, metadata: SourceMetadata):
        external_id = path.read_text().split("=", 1)[1]
        return finalize_batch(
            provider=metadata.provider,
            realm=metadata.realm,
            account_id=metadata.account_id,
            observed_at=metadata.exported_at,
            resources=[cloud_resource(external_id)],
            parser_profile=self.profile_id,
            source_priority=100,
            detail_level=DetailLevel.SUMMARY,
        )


class FakeRegistry:
    def __init__(self) -> None:
        self.parser = FakeParser()
        self.detected: list[Path] = []

    def detect(self, path: Path, metadata: SourceMetadata) -> FakeParser:
        self.detected.append(path)
        assert self.parser.detect(path, metadata).matched
        return self.parser


class FakeArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.opened: list[str] = []
        self.deleted: list[str] = []

    @asynccontextmanager
    async def open(self, object_key: str):
        self.opened.append(object_key)
        if object_key not in self.objects:
            raise FileNotFoundError(object_key)
        yield BytesIO(self.objects[object_key])

    async def delete(self, object_key: str) -> None:
        self.deleted.append(object_key)
        if object_key not in self.objects:
            raise FileNotFoundError(object_key)
        del self.objects[object_key]


class FakeWorkerRepository:
    def __init__(self, import_record: ImportRecord, sources: list[SourceFileRecord]) -> None:
        self.import_record = import_record
        self.sources = sources
        self.parser_metadata: dict[Any, tuple[str, str]] = {}
        self.preview: Any = None
        self.preview_expires_at: datetime | None = None
        self.source_states: dict[Any, str] = {}
        self.run: RunRecord | None = None
        self.checkpoints: list[int] = []
        self.summaries: list[dict[str, Any]] = []
        self.expired: list[Any] = []

    async def get_import(self, import_id):
        return self.import_record if import_id == self.import_record.id else None

    async def get_source_files(self, import_id):
        assert import_id == self.import_record.id
        return list(reversed(self.sources))

    async def mark_source_parsing(self, source_id):
        self.source_states[source_id] = "parsing"

    async def set_source_parser(self, source_id, profile, version):
        self.parser_metadata[source_id] = (profile, version)

    async def save_preview(
        self,
        import_id,
        preview,
        parser_versions,
        expires_at,
    ):
        assert import_id == self.import_record.id
        self.preview = preview
        self.preview_expires_at = expires_at
        assert parser_versions == {"fake.v1": "7"}

    async def mark_sources_preview_ready(self, import_id):
        for source in self.sources:
            self.source_states[source.id] = "preview_ready"

    async def load_preview(self, import_id):
        assert import_id == self.import_record.id
        return self.preview, self.preview_expires_at

    async def get_run(self, run_id):
        return self.run if self.run and run_id == self.run.id else None

    async def mark_run_running(self, run_id):
        assert self.run and run_id == self.run.id

    async def set_run_checkpoint(self, run_id, checkpoint):
        assert self.run and run_id == self.run.id
        self.checkpoints.append(checkpoint)
        self.run.checkpoint = str(checkpoint)

    async def get_active_tag_mappings(self, provider, realm, account_id):
        return []

    async def get_active_owner_mappings(self, provider, realm, account_id):
        return []

    async def finish_run(self, run_id, result):
        assert self.run and run_id == self.run.id
        self.run.status = "succeeded"
        self.summaries = [item.model_dump(mode="json") for item in result.changes]

    async def mark_import_applied(self, import_id):
        for source in self.sources:
            self.source_states[source.id] = "applied"

    async def list_expired_artifacts(self, now):
        return [source for source in self.sources if source.expires_at <= now]

    async def mark_artifact_expired(self, source_id):
        self.expired.append(source_id)


class FakeWriter:
    def __init__(self) -> None:
        self.start_checkpoint: int | None = None

    async def apply(self, preview, checkpoint=0):
        self.start_checkpoint = checkpoint
        return ApplyResult(
            created=1,
            updated=0,
            unchanged=0,
            warnings=0,
            failed=0,
            checkpoint=9,
            changes=[
                AppliedChange(
                    cloud_uid=preview.changes[0].cloud_uid,
                    action="create",
                    changed_fields=["name"],
                    warning_codes=[],
                    netbox_object_id=10,
                )
            ],
        )


def worker_fixture() -> tuple[
    InventoryWorker,
    FakeWorkerRepository,
    FakeArtifactStore,
    FakeWriter,
]:
    import_id = uuid4()
    record = ImportRecord(
        id=import_id,
        provider=Provider.AWS,
        realm=Realm.COMMERCIAL,
        account_id="111111111111",
        export_type="auto",
        region="ap-northeast-2",
        exported_at=NOW,
        request_fingerprint="a" * 64,
        created_at=NOW,
        created_by="test",
    )
    sources = [
        SourceFileRecord(
            id=uuid4(),
            import_id=import_id,
            filename=f"{index}.csv",
            media_type="text/csv",
            sha256=str(index) * 64,
            deduplication_key=str(index + 2) * 64,
            size_bytes=10,
            artifact_key=f"imports/{import_id}/{uuid4()}/{str(index) * 64}",
            expires_at=NOW + timedelta(days=30),
        )
        for index in (1, 2)
    ]
    sources.sort(key=lambda item: item.id)
    repository = FakeWorkerRepository(record, sources)
    artifacts = FakeArtifactStore()
    for index, source in enumerate(sources, start=1):
        artifacts.objects[source.artifact_key] = f"resource=i-{index}".encode()
    fake_writer = FakeWriter()
    worker = InventoryWorker(
        repository=repository,
        artifact_store=artifacts,
        registry=FakeRegistry(),
        current_loader=lambda batch: {},
        writer_factory=lambda **kwargs: fake_writer,
        clock=lambda: NOW,
    )
    return worker, repository, artifacts, fake_writer


@pytest.mark.asyncio
async def test_parse_opens_files_in_id_order_and_saves_immutable_preview() -> None:
    worker, repository, artifacts, _ = worker_fixture()

    await worker.handle_parse_import(
        ClaimedJob(
            id=uuid4(),
            job_type="parse_import",
            payload={"import_id": str(repository.import_record.id)},
            attempts=1,
        )
    )

    expected_order = [
        source.artifact_key
        for source in sorted(repository.sources, key=lambda item: item.id)
    ]
    assert artifacts.opened == expected_order
    assert set(repository.parser_metadata.values()) == {("fake.v1", "7")}
    assert repository.preview is not None
    assert repository.preview.batch_hash
    assert repository.preview_expires_at == NOW + timedelta(hours=24)
    assert set(repository.source_states.values()) == {"preview_ready"}


@pytest.mark.asyncio
async def test_apply_revalidates_hash_and_resumes_from_checkpoint() -> None:
    worker, repository, _, writer = worker_fixture()
    await worker.handle_parse_import(
        ClaimedJob(
            id=uuid4(),
            job_type="parse_import",
            payload={"import_id": str(repository.import_record.id)},
            attempts=1,
        )
    )
    run_id = uuid4()
    repository.run = RunRecord(
        id=run_id,
        import_id=repository.import_record.id,
        batch_hash=repository.preview.batch_hash,
        apply_valid_only=False,
        status="queued",
        checkpoint="3",
        summary={},
        started_at=None,
        finished_at=None,
    )

    await worker.handle_apply_import(
        ClaimedJob(
            id=uuid4(),
            job_type="apply_import",
            payload={"run_id": str(run_id)},
            attempts=1,
        )
    )

    assert writer.start_checkpoint == 3
    assert repository.run.status == "succeeded"
    assert repository.summaries[0]["action"] == "create"
    assert set(repository.source_states.values()) == {"applied"}


@pytest.mark.asyncio
async def test_artifact_expiry_is_idempotent_and_preserves_processing_state() -> None:
    worker, repository, artifacts, _ = worker_fixture()
    expired = repository.sources[0]
    retained = repository.sources[1]
    expired.expires_at = NOW
    retained.expires_at = NOW + timedelta(seconds=1)
    repository.source_states[expired.id] = "preview_ready"
    repository.source_states[retained.id] = "preview_ready"
    artifacts.objects.pop(expired.artifact_key)

    count = await worker.expire_artifacts(NOW)

    assert count == 1
    assert repository.expired == [expired.id]
    assert repository.source_states[expired.id] == "preview_ready"
    assert retained.artifact_key in artifacts.objects
