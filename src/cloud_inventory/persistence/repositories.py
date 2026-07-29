from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cloud_inventory.domain.models import Provider, Realm
from cloud_inventory.ingest.file_validation import MediaType
from cloud_inventory.persistence.models import (
    ArtifactStatus,
    ChangeSummary,
    CollectionJob,
    CollectionRun,
    ImportPreview,
    ImportRequest,
    JobStatus,
    OwnerMapping,
    PreviewChange,
    PreviewStatus,
    RunStatus,
    SourceFile,
    SourceFileStatus,
    TagMapping,
)

if TYPE_CHECKING:
    from cloud_inventory.api.imports import (
        ImportRecord,
        PreviewPage,
        RunRecord,
        SourceFileRecord,
    )
    from cloud_inventory.api.mappings import MappingRecord


class InvalidStateTransitionError(ValueError):
    """A persisted lifecycle object cannot move to the requested state."""


_SOURCE_FILE_TRANSITIONS = {
    SourceFileStatus.UPLOADED: {
        SourceFileStatus.PARSING,
        SourceFileStatus.FAILED,
    },
    SourceFileStatus.PARSING: {
        SourceFileStatus.PREVIEW_READY,
        SourceFileStatus.FAILED,
    },
    SourceFileStatus.PREVIEW_READY: {SourceFileStatus.APPLYING},
    SourceFileStatus.APPLYING: {
        SourceFileStatus.APPLIED,
        SourceFileStatus.FAILED,
    },
}
_ARTIFACT_TRANSITIONS = {
    ArtifactStatus.AVAILABLE: {ArtifactStatus.EXPIRED},
}
_PREVIEW_TRANSITIONS = {
    PreviewStatus.READY: {
        PreviewStatus.APPLYING,
        PreviewStatus.EXPIRED,
    },
    PreviewStatus.APPLYING: {PreviewStatus.APPLIED},
}
_JOB_TRANSITIONS = {
    JobStatus.QUEUED: {JobStatus.RUNNING},
    JobStatus.RUNNING: {
        JobStatus.SUCCEEDED,
        JobStatus.RETRY_WAIT,
        JobStatus.FAILED,
    },
    JobStatus.RETRY_WAIT: {JobStatus.QUEUED},
}


def _require_transition(
    current: Any,
    target: Any,
    transitions: dict[Any, set[Any]],
) -> None:
    if target not in transitions.get(current, set()):
        raise InvalidStateTransitionError(f"invalid state transition: {current} -> {target}")


def require_source_file_transition(
    current: SourceFileStatus,
    target: SourceFileStatus,
) -> None:
    _require_transition(current, target, _SOURCE_FILE_TRANSITIONS)


def require_artifact_transition(
    current: ArtifactStatus,
    target: ArtifactStatus,
) -> None:
    _require_transition(current, target, _ARTIFACT_TRANSITIONS)


def require_preview_transition(
    current: PreviewStatus,
    target: PreviewStatus,
) -> None:
    _require_transition(current, target, _PREVIEW_TRANSITIONS)


def require_job_transition(current: JobStatus, target: JobStatus) -> None:
    _require_transition(current, target, _JOB_TRANSITIONS)


def _hash_components(components: Sequence[str]) -> str:
    return hashlib.sha256("\0".join(components).encode()).hexdigest()


def build_source_file_deduplication_key(
    provider: str,
    realm: str,
    account_id: str,
    file_sha256: str,
) -> str:
    return _hash_components((provider, realm, account_id, file_sha256))


def build_request_fingerprint(
    provider: str,
    realm: str,
    account_id: str,
    file_sha256_values: Sequence[str],
) -> str:
    return _hash_components(
        (provider, realm, account_id, *sorted(file_sha256_values))
    )


def normalize_mapping_key(value: str) -> str:
    return value.strip().casefold()


def normalize_mapping_value(value: str) -> str:
    return value.strip()


class ImportRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _import_record(
        row: ImportRequest,
        parse_job_id: UUID | None,
    ) -> ImportRecord:
        from cloud_inventory.api.imports import ImportRecord

        return ImportRecord(
            id=row.id,
            provider=Provider(row.provider),
            realm=Realm(row.realm),
            account_id=row.account_id,
            export_type=row.export_type,
            region=row.region,
            exported_at=row.exported_at,
            request_fingerprint=row.request_fingerprint,
            created_at=row.created_at,
            created_by=row.created_by,
            parse_job_id=parse_job_id,
        )

    async def find_import_by_fingerprint(
        self,
        fingerprint: str,
    ) -> ImportRecord | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ImportRequest).where(
                        ImportRequest.request_fingerprint == fingerprint
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            parse_job_id = (
                await session.execute(
                    select(CollectionJob.id).where(
                        CollectionJob.idempotency_key
                        == f"parse:{row.id}:{row.request_fingerprint}"
                    )
                )
            ).scalar_one_or_none()
            return self._import_record(row, parse_job_id)

    async def get_import(self, import_id: UUID) -> ImportRecord | None:
        async with self._session_factory() as session:
            row = await session.get(ImportRequest, import_id)
            if row is None:
                return None
            parse_job_id = (
                await session.execute(
                    select(CollectionJob.id).where(
                        CollectionJob.idempotency_key
                        == f"parse:{row.id}:{row.request_fingerprint}"
                    )
                )
            ).scalar_one_or_none()
            return self._import_record(row, parse_job_id)

    async def find_source_conflicts(
        self,
        deduplication_keys: list[str],
    ) -> list[SourceFileRecord]:
        from cloud_inventory.api.imports import SourceFileRecord

        if not deduplication_keys:
            return []
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(SourceFile)
                    .where(SourceFile.deduplication_key.in_(deduplication_keys))
                    .order_by(SourceFile.id)
                )
            ).scalars()
            return [
                SourceFileRecord(
                    id=row.id,
                    import_id=row.import_id,
                    filename=row.filename,
                    media_type=cast(MediaType, row.media_type),
                    sha256=row.sha256,
                    deduplication_key=row.deduplication_key,
                    size_bytes=row.size_bytes,
                    artifact_key=row.artifact_key,
                    expires_at=row.expires_at,
                    parser_profile=row.parser_profile,
                    parser_version=row.parser_version,
                    status=row.status.value,
                    artifact_status=row.artifact_status.value,
                )
                for row in rows
            ]

    async def create_import(
        self,
        record: ImportRecord,
        source_files: list[SourceFileRecord],
    ) -> None:
        job_id = uuid4()
        try:
            async with self._session_factory.begin() as session:
                session.add(
                    ImportRequest(
                        id=record.id,
                        provider=record.provider.value,
                        realm=record.realm.value,
                        account_id=record.account_id,
                        export_type=record.export_type,
                        region=record.region,
                        exported_at=record.exported_at,
                        request_fingerprint=record.request_fingerprint,
                        created_at=record.created_at,
                        created_by=record.created_by,
                    )
                )
                for source in source_files:
                    session.add(
                        SourceFile(
                            id=source.id,
                            import_id=source.import_id,
                            filename=source.filename,
                            media_type=source.media_type,
                            sha256=source.sha256,
                            deduplication_key=source.deduplication_key,
                            size_bytes=source.size_bytes,
                            artifact_key=source.artifact_key,
                            status=SourceFileStatus.UPLOADED,
                            artifact_status=ArtifactStatus.AVAILABLE,
                            expires_at=source.expires_at,
                        )
                    )
                session.add(
                    CollectionJob(
                        id=job_id,
                        job_type="parse_import",
                        payload={"import_id": str(record.id)},
                        idempotency_key=(
                            f"parse:{record.id}:{record.request_fingerprint}"
                        ),
                        status=JobStatus.QUEUED,
                        attempts=0,
                        available_at=record.created_at,
                    )
                )
                await session.flush()
        except IntegrityError:
            raise
        record.parse_job_id = job_id

    async def delete_import(self, import_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            row = await session.get(ImportRequest, import_id)
            if row is not None:
                await session.execute(
                    delete(CollectionJob).where(
                        CollectionJob.idempotency_key
                        == f"parse:{row.id}:{row.request_fingerprint}"
                    )
                )
                await session.delete(row)

    async def get_source_files(self, import_id: UUID) -> list[SourceFileRecord]:
        from cloud_inventory.api.imports import SourceFileRecord

        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(SourceFile)
                    .where(SourceFile.import_id == import_id)
                    .order_by(SourceFile.id)
                )
            ).scalars()
            return [
                SourceFileRecord(
                    id=row.id,
                    import_id=row.import_id,
                    filename=row.filename,
                    media_type=cast(MediaType, row.media_type),
                    sha256=row.sha256,
                    deduplication_key=row.deduplication_key,
                    size_bytes=row.size_bytes,
                    artifact_key=row.artifact_key,
                    expires_at=row.expires_at,
                    parser_profile=row.parser_profile,
                    parser_version=row.parser_version,
                    status=row.status.value,
                    artifact_status=row.artifact_status.value,
                )
                for row in rows
            ]

    async def mark_source_parsing(self, source_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            source = await session.get(SourceFile, source_id, with_for_update=True)
            if source is None:
                raise ValueError("source file does not exist")
            if source.status is not SourceFileStatus.PARSING:
                require_source_file_transition(
                    source.status,
                    SourceFileStatus.PARSING,
                )
                source.status = SourceFileStatus.PARSING

    async def set_source_parser(
        self,
        source_id: UUID,
        profile: str,
        version: str,
    ) -> None:
        async with self._session_factory.begin() as session:
            source = await session.get(SourceFile, source_id, with_for_update=True)
            if source is None:
                raise ValueError("source file does not exist")
            source.parser_profile = profile
            source.parser_version = version

    async def save_preview(
        self,
        import_id: UUID,
        preview: Any,
        parser_versions: dict[str, str],
        expires_at: datetime,
    ) -> None:
        async with self._session_factory.begin() as session:
            existing = (
                await session.execute(
                    select(ImportPreview)
                    .where(ImportPreview.import_id == import_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.batch_hash != preview.batch_hash:
                    raise ValueError("immutable preview Batch hash changed")
                return
            row = ImportPreview(
                import_id=import_id,
                batch_hash=preview.batch_hash,
                parser_versions=parser_versions,
                summary={
                    "create": preview.created,
                    "update": preview.updated,
                    "unchanged": preview.unchanged,
                    "warning": preview.warnings,
                    "error": preview.errors,
                },
                expires_at=expires_at,
                status=PreviewStatus.READY,
            )
            session.add(row)
            await session.flush()
            for ordinal, change in enumerate(
                sorted(preview.changes, key=lambda item: item.cloud_uid)
            ):
                session.add(
                    PreviewChange(
                        preview_id=row.id,
                        ordinal=ordinal,
                        cloud_uid=change.cloud_uid,
                        resource_type=change.resource_type.value,
                        action=change.action.value,
                        changed_fields=change.changed_fields,
                        warning_codes=change.warnings,
                        desired=change.desired.model_dump(mode="json"),
                    )
                )

    async def mark_sources_preview_ready(self, import_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            sources = (
                await session.execute(
                    select(SourceFile)
                    .where(SourceFile.import_id == import_id)
                    .with_for_update()
                )
            ).scalars()
            for source in sources:
                if source.status is not SourceFileStatus.PREVIEW_READY:
                    require_source_file_transition(
                        source.status,
                        SourceFileStatus.PREVIEW_READY,
                    )
                    source.status = SourceFileStatus.PREVIEW_READY

    async def load_preview(
        self,
        import_id: UUID,
    ) -> tuple[Any, datetime] | None:
        from cloud_inventory.domain.models import CloudResource, ResourceType
        from cloud_inventory.reconciliation.diff import (
            ChangeAction,
            PreviewResult,
            ResourceChange,
        )

        async with self._session_factory() as session:
            preview = (
                await session.execute(
                    select(ImportPreview).where(ImportPreview.import_id == import_id)
                )
            ).scalar_one_or_none()
            if preview is None:
                return None
            rows = (
                await session.execute(
                    select(PreviewChange)
                    .where(PreviewChange.preview_id == preview.id)
                    .order_by(PreviewChange.ordinal)
                )
            ).scalars()
            changes = [
                ResourceChange(
                    cloud_uid=row.cloud_uid,
                    resource_type=ResourceType(row.resource_type),
                    action=ChangeAction(row.action),
                    changed_fields=row.changed_fields,
                    warnings=row.warning_codes,
                    desired=CloudResource.model_validate(row.desired),
                )
                for row in rows
            ]
            result = PreviewResult(
                batch_hash=preview.batch_hash,
                created=int(preview.summary.get("create", 0)),
                updated=int(preview.summary.get("update", 0)),
                unchanged=int(preview.summary.get("unchanged", 0)),
                warnings=int(preview.summary.get("warning", 0)),
                errors=int(preview.summary.get("error", 0)),
                changes=changes,
            )
            return result, preview.expires_at

    async def insert_preview(
        self,
        *,
        import_id: UUID,
        batch_hash: str,
        parser_versions: dict[str, Any],
        summary: dict[str, Any],
        expires_at: datetime,
        changes: Sequence[dict[str, Any]],
    ) -> UUID:
        async with self._session_factory.begin() as session:
            preview = ImportPreview(
                import_id=import_id,
                batch_hash=batch_hash,
                parser_versions=parser_versions,
                summary=summary,
                expires_at=expires_at,
                status=PreviewStatus.READY,
            )
            session.add(preview)
            await session.flush()
            for ordinal, change in enumerate(
                sorted(changes, key=lambda item: str(item["cloud_uid"]))
            ):
                session.add(
                    PreviewChange(
                        preview_id=preview.id,
                        ordinal=ordinal,
                        cloud_uid=str(change["cloud_uid"]),
                        resource_type=str(change["resource_type"]),
                        action=str(change["action"]),
                        changed_fields=list(change.get("changed_fields", [])),
                        warning_codes=list(change.get("warning_codes", [])),
                        desired=dict(change.get("desired", {})),
                    )
                )
            return preview.id

    async def get_preview_page(
        self,
        import_id: UUID,
        offset: int,
        limit: int,
    ) -> PreviewPage | None:
        from cloud_inventory.api.imports import PreviewPage

        async with self._session_factory() as session:
            preview = (
                await session.execute(
                    select(ImportPreview).where(ImportPreview.import_id == import_id)
                )
            ).scalar_one_or_none()
            if preview is None:
                return None
            total = (
                await session.execute(
                    select(func.count(PreviewChange.id)).where(
                        PreviewChange.preview_id == preview.id
                    )
                )
            ).scalar_one()
            changes = (
                await session.execute(
                    select(PreviewChange)
                    .where(PreviewChange.preview_id == preview.id)
                    .order_by(PreviewChange.ordinal)
                    .offset(offset)
                    .limit(limit)
                )
            ).scalars()
            return PreviewPage(
                import_id=import_id,
                batch_hash=preview.batch_hash,
                expires_at=preview.expires_at,
                summary={
                    str(key): int(value)
                    for key, value in preview.summary.items()
                },
                total_changes=total,
                offset=offset,
                limit=limit,
                changes=[
                    {
                        "cloud_uid": change.cloud_uid,
                        "resource_type": change.resource_type,
                        "action": change.action,
                        "changed_fields": change.changed_fields,
                        "warning_codes": change.warning_codes,
                        "desired": change.desired,
                    }
                    for change in changes
                ],
            )

    @staticmethod
    def _run_record(row: CollectionRun) -> RunRecord:
        from cloud_inventory.api.imports import RunRecord

        return RunRecord(
            id=row.id,
            import_id=row.import_id,
            batch_hash=row.batch_hash,
            apply_valid_only=row.apply_valid_only,
            status=row.status.value,
            checkpoint=row.checkpoint,
            summary=row.summary,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    async def create_or_get_run(
        self,
        *,
        import_id: UUID,
        batch_hash: str,
        apply_valid_only: bool,
        idempotency_key: str,
    ) -> tuple[RunRecord, bool]:
        async with self._session_factory.begin() as session:
            existing = (
                await session.execute(
                    select(CollectionRun).where(
                        CollectionRun.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return self._run_record(existing), False

            run = CollectionRun(
                id=uuid4(),
                import_id=import_id,
                batch_hash=batch_hash,
                apply_valid_only=apply_valid_only,
                idempotency_key=idempotency_key,
                status=RunStatus.QUEUED,
                summary={},
            )
            session.add(run)
            session.add(
                CollectionJob(
                    id=uuid4(),
                    job_type="apply_import",
                    payload={"run_id": str(run.id)},
                    idempotency_key=idempotency_key,
                    status=JobStatus.QUEUED,
                    attempts=0,
                    available_at=datetime.now().astimezone(),
                )
            )
            await session.flush()
            return self._run_record(run), True

    async def delete_run(self, run_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            row = await session.get(CollectionRun, run_id)
            if row is not None:
                await session.execute(
                    delete(CollectionJob).where(
                        CollectionJob.idempotency_key == row.idempotency_key
                    )
                )
                await session.delete(row)

    async def get_run(self, run_id: UUID) -> RunRecord | None:
        async with self._session_factory() as session:
            row = await session.get(CollectionRun, run_id)
            return self._run_record(row) if row is not None else None

    async def mark_run_running(self, run_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            run = await session.get(CollectionRun, run_id, with_for_update=True)
            if run is None:
                raise ValueError("collection run does not exist")
            if run.status is RunStatus.QUEUED:
                run.status = RunStatus.RUNNING
                run.started_at = datetime.now(UTC)
            elif run.status is not RunStatus.RUNNING:
                raise InvalidStateTransitionError(
                    f"invalid run state for apply: {run.status}"
                )

            preview = (
                await session.execute(
                    select(ImportPreview)
                    .where(ImportPreview.import_id == run.import_id)
                    .with_for_update()
                )
            ).scalar_one()
            if preview.status is PreviewStatus.READY:
                require_preview_transition(
                    preview.status,
                    PreviewStatus.APPLYING,
                )
                preview.status = PreviewStatus.APPLYING
            elif preview.status is not PreviewStatus.APPLYING:
                raise InvalidStateTransitionError(
                    f"invalid preview state for apply: {preview.status}"
                )

            sources = (
                await session.execute(
                    select(SourceFile)
                    .where(SourceFile.import_id == run.import_id)
                    .with_for_update()
                )
            ).scalars()
            for source in sources:
                if source.status is SourceFileStatus.PREVIEW_READY:
                    require_source_file_transition(
                        source.status,
                        SourceFileStatus.APPLYING,
                    )
                    source.status = SourceFileStatus.APPLYING
                elif source.status is not SourceFileStatus.APPLYING:
                    raise InvalidStateTransitionError(
                        f"invalid source state for apply: {source.status}"
                    )

    async def set_run_checkpoint(self, run_id: UUID, checkpoint: int) -> None:
        async with self._session_factory.begin() as session:
            run = await session.get(CollectionRun, run_id, with_for_update=True)
            if run is None or run.status is not RunStatus.RUNNING:
                raise ValueError("cannot checkpoint a run that is not running")
            previous = int(run.checkpoint or 0)
            if checkpoint < previous:
                raise ValueError("run checkpoint cannot move backwards")
            run.checkpoint = str(checkpoint)

    async def get_active_tag_mappings(
        self,
        provider: str,
        realm: str,
        account_id: str,
    ) -> Sequence[TagMapping]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(TagMapping)
                    .where(
                        TagMapping.provider == provider,
                        TagMapping.realm == realm,
                        TagMapping.account_id == account_id,
                        TagMapping.enabled.is_(True),
                    )
                    .order_by(TagMapping.priority.desc(), TagMapping.id)
                )
            ).scalars()
            return list(rows)

    async def get_active_owner_mappings(
        self,
        provider: str,
        realm: str,
        account_id: str,
    ) -> Sequence[OwnerMapping]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(OwnerMapping)
                    .where(
                        OwnerMapping.provider == provider,
                        OwnerMapping.realm == realm,
                        OwnerMapping.account_id == account_id,
                        OwnerMapping.enabled.is_(True),
                    )
                    .order_by(OwnerMapping.priority.desc(), OwnerMapping.id)
                )
            ).scalars()
            return list(rows)

    async def finish_run(self, run_id: UUID, result: Any) -> None:
        async with self._session_factory.begin() as session:
            run = await session.get(CollectionRun, run_id, with_for_update=True)
            if run is None or run.status is not RunStatus.RUNNING:
                raise ValueError("cannot finish a run that is not running")
            await session.execute(
                delete(ChangeSummary).where(ChangeSummary.run_id == run_id)
            )
            for item in result.changes:
                session.add(
                    ChangeSummary(
                        run_id=run_id,
                        cloud_uid=item.cloud_uid,
                        action=item.action,
                        changed_fields=item.changed_fields,
                        warning_codes=item.warning_codes,
                    )
                )
            run.status = (
                RunStatus.FAILED if result.failed else RunStatus.SUCCEEDED
            )
            run.checkpoint = str(result.checkpoint)
            run.summary = {
                "create": result.created,
                "update": result.updated,
                "unchanged": result.unchanged,
                "warning": result.warnings,
                "error": result.failed,
            }
            run.finished_at = datetime.now(UTC)

    async def fail_run(self, run_id: UUID, error: Exception) -> None:
        async with self._session_factory.begin() as session:
            run = await session.get(CollectionRun, run_id, with_for_update=True)
            if run is None or run.status is RunStatus.SUCCEEDED:
                return
            run.status = RunStatus.FAILED
            run.summary = {"error": 1, "message": error.__class__.__name__}
            run.finished_at = datetime.now(UTC)

    async def mark_import_applied(self, import_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            preview = (
                await session.execute(
                    select(ImportPreview)
                    .where(ImportPreview.import_id == import_id)
                    .with_for_update()
                )
            ).scalar_one()
            if preview.status is not PreviewStatus.APPLIED:
                require_preview_transition(preview.status, PreviewStatus.APPLIED)
                preview.status = PreviewStatus.APPLIED
            sources = (
                await session.execute(
                    select(SourceFile)
                    .where(SourceFile.import_id == import_id)
                    .with_for_update()
                )
            ).scalars()
            for source in sources:
                if source.status is not SourceFileStatus.APPLIED:
                    require_source_file_transition(
                        source.status,
                        SourceFileStatus.APPLIED,
                    )
                    source.status = SourceFileStatus.APPLIED

    async def list_expired_artifacts(
        self,
        now: datetime,
    ) -> list[SourceFileRecord]:
        from cloud_inventory.api.imports import SourceFileRecord

        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(SourceFile)
                    .where(
                        SourceFile.expires_at <= now,
                        SourceFile.artifact_status == ArtifactStatus.AVAILABLE,
                    )
                    .order_by(SourceFile.id)
                )
            ).scalars()
            return [
                SourceFileRecord(
                    id=row.id,
                    import_id=row.import_id,
                    filename=row.filename,
                    media_type=cast(MediaType, row.media_type),
                    sha256=row.sha256,
                    deduplication_key=row.deduplication_key,
                    size_bytes=row.size_bytes,
                    artifact_key=row.artifact_key,
                    expires_at=row.expires_at,
                    parser_profile=row.parser_profile,
                    parser_version=row.parser_version,
                    status=row.status.value,
                    artifact_status=row.artifact_status.value,
                )
                for row in rows
            ]

    async def mark_artifact_expired(self, source_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            source = await session.get(SourceFile, source_id, with_for_update=True)
            if source is None:
                return
            if source.artifact_status is ArtifactStatus.EXPIRED:
                return
            require_artifact_transition(
                source.artifact_status,
                ArtifactStatus.EXPIRED,
            )
            source.artifact_status = ArtifactStatus.EXPIRED

    @staticmethod
    def _tag_mapping_record(row: TagMapping) -> MappingRecord:
        from cloud_inventory.api.mappings import MappingRecord

        return MappingRecord(
            id=row.id,
            values={
                "provider": row.provider,
                "realm": row.realm,
                "account_id": row.account_id,
                "source_key": row.source_key,
                "source_value": row.source_value,
                "business_service_code": row.business_service_code,
                "priority": row.priority,
                "enabled": row.enabled,
            },
        )

    @staticmethod
    def _owner_mapping_record(row: OwnerMapping) -> MappingRecord:
        from cloud_inventory.api.mappings import MappingRecord

        return MappingRecord(
            id=row.id,
            values={
                "provider": row.provider,
                "realm": row.realm,
                "account_id": row.account_id,
                "source_value": row.source_value,
                "netbox_owner_id": row.netbox_owner_id,
                "priority": row.priority,
                "enabled": row.enabled,
            },
        )

    async def list_tag_mappings(self, **filters: Any) -> list[MappingRecord]:
        statement = select(TagMapping)
        for name in ("provider", "realm", "account_id", "enabled"):
            value = filters.get(name)
            if value is not None:
                statement = statement.where(getattr(TagMapping, name) == value)
        statement = (
            statement.order_by(TagMapping.priority.desc(), TagMapping.id)
            .offset(int(filters.get("offset", 0)))
            .limit(int(filters.get("limit", 100)))
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars()
            return [self._tag_mapping_record(row) for row in rows]

    async def list_owner_mappings(self, **filters: Any) -> list[MappingRecord]:
        statement = select(OwnerMapping)
        for name in ("provider", "realm", "account_id", "enabled"):
            value = filters.get(name)
            if value is not None:
                statement = statement.where(getattr(OwnerMapping, name) == value)
        statement = (
            statement.order_by(OwnerMapping.priority.desc(), OwnerMapping.id)
            .offset(int(filters.get("offset", 0)))
            .limit(int(filters.get("limit", 100)))
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars()
            return [self._owner_mapping_record(row) for row in rows]

    async def upsert_tag_mapping(
        self,
        mapping_id: UUID,
        values: dict[str, Any],
    ) -> tuple[MappingRecord, bool]:
        from cloud_inventory.api.mappings import MappingConflictError

        try:
            async with self._session_factory.begin() as session:
                row = await session.get(TagMapping, mapping_id, with_for_update=True)
                created = row is None
                if row is None:
                    row = TagMapping(id=mapping_id, **values)
                    session.add(row)
                else:
                    for name, value in values.items():
                        setattr(row, name, value)
                await session.flush()
                record = self._tag_mapping_record(row)
        except IntegrityError as error:
            raise MappingConflictError("duplicate active tag mapping") from error
        return record, created

    async def upsert_owner_mapping(
        self,
        mapping_id: UUID,
        values: dict[str, Any],
    ) -> tuple[MappingRecord, bool]:
        from cloud_inventory.api.mappings import MappingConflictError

        try:
            async with self._session_factory.begin() as session:
                row = await session.get(OwnerMapping, mapping_id, with_for_update=True)
                created = row is None
                if row is None:
                    row = OwnerMapping(id=mapping_id, **values)
                    session.add(row)
                else:
                    for name, value in values.items():
                        setattr(row, name, value)
                await session.flush()
                record = self._owner_mapping_record(row)
        except IntegrityError as error:
            raise MappingConflictError("duplicate active owner mapping") from error
        return record, created
