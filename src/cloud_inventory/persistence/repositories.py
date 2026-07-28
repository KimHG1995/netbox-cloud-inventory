import hashlib
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cloud_inventory.persistence.models import (
    ArtifactStatus,
    ImportPreview,
    JobStatus,
    PreviewChange,
    PreviewStatus,
    SourceFileStatus,
)


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
