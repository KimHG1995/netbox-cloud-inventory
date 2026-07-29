from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Protocol
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from cloud_inventory.api.dependencies import (
    get_artifact_store,
    get_import_repository,
    get_job_queue,
)
from cloud_inventory.config import Settings, get_settings
from cloud_inventory.domain.models import Provider, Realm
from cloud_inventory.ingest.artifact_store import ArtifactStore, build_artifact_key
from cloud_inventory.ingest.file_validation import MediaType, ValidatedFile, validate_upload
from cloud_inventory.persistence.repositories import (
    build_request_fingerprint,
    build_source_file_deduplication_key,
)

router = APIRouter()

PARSER_PROFILES = frozenset(
    {
        "canonical.import_bundle.v1",
        "aws.resource_explorer.csv.v1",
        "ncp.server_list.xlsx.v1",
        "ncp.public_ip_list.xlsx.v1",
        "ncp.load_balancer_list.xlsx.v1",
        "ncp.object_storage_bucket_list.xlsx.v1",
    }
)
EXPECTED_MEDIA_TYPES: dict[str, MediaType] = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class ImportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    provider: Provider
    realm: Realm
    account_id: str
    export_type: str
    region: str | None
    exported_at: AwareDatetime
    request_fingerprint: str
    created_at: AwareDatetime
    created_by: str
    parse_job_id: UUID | None = None


class SourceFileRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    import_id: UUID
    filename: str
    media_type: MediaType
    sha256: str
    deduplication_key: str
    size_bytes: int
    artifact_key: str
    expires_at: AwareDatetime
    parser_profile: str | None = None
    parser_version: str | None = None
    status: str = "uploaded"
    artifact_status: str = "available"


class PreviewPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_id: UUID
    batch_hash: str
    expires_at: AwareDatetime
    summary: dict[str, int]
    total_changes: int
    offset: int
    limit: int
    changes: list[dict[str, Any]]


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    import_id: UUID
    batch_hash: str
    apply_valid_only: bool
    status: str
    checkpoint: str | None
    summary: dict[str, Any]
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    apply_valid_only: bool


class ImportRepositoryPort(Protocol):
    async def find_import_by_fingerprint(
        self,
        fingerprint: str,
    ) -> ImportRecord | None: ...

    async def find_source_conflicts(
        self,
        deduplication_keys: list[str],
    ) -> list[SourceFileRecord]: ...

    async def create_import(
        self,
        record: ImportRecord,
        source_files: list[SourceFileRecord],
    ) -> None: ...

    async def delete_import(self, import_id: UUID) -> None: ...

    async def get_preview_page(
        self,
        import_id: UUID,
        offset: int,
        limit: int,
    ) -> PreviewPage | None: ...

    async def create_or_get_run(
        self,
        *,
        import_id: UUID,
        batch_hash: str,
        apply_valid_only: bool,
        idempotency_key: str,
    ) -> tuple[RunRecord, bool]: ...

    async def delete_run(self, run_id: UUID) -> None: ...

    async def get_run(self, run_id: UUID) -> RunRecord | None: ...


class JobQueuePort(Protocol):
    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> UUID: ...


def _detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _validate_media_type(
    validated: ValidatedFile,
    upload: UploadFile,
) -> None:
    suffix = Path(validated.original_filename).suffix.casefold()
    expected = EXPECTED_MEDIA_TYPES.get(suffix)
    if expected is None or expected != validated.media_type:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=_detail(
                "filename_content_mismatch",
                "filename extension does not match the detected content",
            ),
        )
    if upload.content_type != validated.media_type:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=_detail(
                "content_type_mismatch",
                "declared content type does not match the detected content",
            ),
        )


def _close_validated(files: Sequence[ValidatedFile]) -> None:
    for validated in files:
        if not validated.stream.closed:
            validated.stream.close()


def _upload_response(record: ImportRecord, job_id: UUID) -> dict[str, str]:
    return {"import_id": str(record.id), "parse_job_id": str(job_id)}


@router.post("/imports")
async def create_import(
    files: Annotated[list[UploadFile], File()],
    provider: Annotated[Provider, Form()],
    realm: Annotated[Realm, Form()],
    account_id: Annotated[str, Form(min_length=1, max_length=128)],
    export_type: Annotated[str, Form(min_length=1, max_length=64)],
    exported_at: Annotated[datetime, Form()],
    region: Annotated[str | None, Form(max_length=64)] = None,
    *,
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[ImportRepositoryPort, Depends(get_import_repository)],
    queue: Annotated[JobQueuePort, Depends(get_job_queue)],
    artifact_store: Annotated[ArtifactStore, Depends(get_artifact_store)],
) -> JSONResponse:
    if not files or len(files) > settings.max_files_per_import:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_detail("invalid_file_count", "invalid number of upload files"),
        )
    if export_type != "auto" and export_type not in PARSER_PROFILES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_detail("unknown_export_type", "unknown export type"),
        )
    if exported_at.tzinfo is None or exported_at.utcoffset() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_detail("naive_exported_at", "exported_at must include a timezone"),
        )
    now = datetime.now(UTC)
    if exported_at.astimezone(UTC) > now + timedelta(minutes=5):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_detail("future_exported_at", "exported_at is too far in the future"),
        )

    validated_files: list[ValidatedFile] = []
    try:
        for upload in files:
            try:
                validated = validate_upload(
                    upload.file,
                    upload.filename or "",
                    settings.max_file_bytes,
                )
            except ValueError as error:
                error_text = str(error)
                status_code = (
                    status.HTTP_413_CONTENT_TOO_LARGE
                    if "exceeds size limit" in error_text
                    else status.HTTP_422_UNPROCESSABLE_CONTENT
                )
                raise HTTPException(
                    status_code=status_code,
                    detail=_detail("invalid_upload", error_text),
                ) from error
            _validate_media_type(validated, upload)
            validated_files.append(validated)

        request_fingerprint = build_request_fingerprint(
            provider.value,
            realm.value,
            account_id,
            [item.sha256 for item in validated_files],
        )
        existing = await repository.find_import_by_fingerprint(request_fingerprint)
        if existing is not None:
            if existing.parse_job_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=_detail(
                        "duplicate_import_incomplete",
                        "the original import has no parse job",
                    ),
                )
            return JSONResponse(
                _upload_response(existing, existing.parse_job_id),
                status_code=status.HTTP_200_OK,
            )

        deduplication_keys = [
            build_source_file_deduplication_key(
                provider.value,
                realm.value,
                account_id,
                item.sha256,
            )
            for item in validated_files
        ]
        conflicts = await repository.find_source_conflicts(deduplication_keys)
        if conflicts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_detail(
                    "duplicate_file_in_different_import",
                    "one or more files already belong to another import",
                ),
            )

        import_id = uuid4()
        source_files: list[SourceFileRecord] = []
        stored_keys: list[str] = []
        for validated, deduplication_key in zip(
            validated_files,
            deduplication_keys,
            strict=True,
        ):
            source_file_id = uuid4()
            artifact_key = build_artifact_key(
                import_id,
                source_file_id,
                validated.sha256,
            )
            await artifact_store.put(validated, artifact_key)
            stored_keys.append(artifact_key)
            source_files.append(
                SourceFileRecord(
                    id=source_file_id,
                    import_id=import_id,
                    filename=validated.original_filename,
                    media_type=validated.media_type,
                    sha256=validated.sha256,
                    deduplication_key=deduplication_key,
                    size_bytes=validated.size_bytes,
                    artifact_key=artifact_key,
                    expires_at=now
                    + timedelta(days=settings.artifact_retention_days),
                )
            )

        record = ImportRecord(
            id=import_id,
            provider=provider,
            realm=realm,
            account_id=account_id,
            export_type=export_type,
            region=region,
            exported_at=exported_at,
            request_fingerprint=request_fingerprint,
            created_at=now,
            created_by="manual-api",
        )
        try:
            await repository.create_import(record, source_files)
            job_id = await queue.enqueue(
                "parse_import",
                {"import_id": str(import_id)},
                f"parse:{import_id}:{request_fingerprint}",
            )
        except Exception:
            await repository.delete_import(import_id)
            for object_key in stored_keys:
                await artifact_store.delete(object_key)
            raise
        record.parse_job_id = job_id
        return JSONResponse(
            _upload_response(record, job_id),
            status_code=status.HTTP_202_ACCEPTED,
        )
    finally:
        _close_validated(validated_files)


async def _ready_preview(
    repository: ImportRepositoryPort,
    import_id: UUID,
    *,
    offset: int = 0,
    limit: int = 100,
) -> PreviewPage:
    preview = await repository.get_preview_page(import_id, offset, limit)
    if preview is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_detail("preview_not_ready", "preview is not ready"),
        )
    if preview.expires_at <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=_detail("preview_expired", "preview has expired"),
        )
    return preview


@router.get("/imports/{import_id}/preview", response_model=PreviewPage)
async def get_preview(
    import_id: UUID,
    repository: Annotated[ImportRepositoryPort, Depends(get_import_repository)],
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=500)] = 100,
) -> PreviewPage:
    return await _ready_preview(
        repository,
        import_id,
        offset=offset,
        limit=limit,
    )


@router.post("/imports/{import_id}/apply")
async def apply_import(
    import_id: UUID,
    request: ApplyRequest,
    repository: Annotated[ImportRepositoryPort, Depends(get_import_repository)],
    queue: Annotated[JobQueuePort, Depends(get_job_queue)],
) -> JSONResponse:
    preview = await _ready_preview(repository, import_id)
    if preview.batch_hash != request.batch_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_detail("batch_hash_changed", "preview Batch hash has changed"),
        )
    if preview.summary.get("error", 0) and not request.apply_valid_only:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_detail("preview_has_errors", "preview errors block apply"),
        )
    bool_value = str(request.apply_valid_only).lower()
    idempotency_key = (
        f"apply:{import_id}:{request.batch_hash}:{bool_value}"
    )
    run, created = await repository.create_or_get_run(
        import_id=import_id,
        batch_hash=request.batch_hash,
        apply_valid_only=request.apply_valid_only,
        idempotency_key=idempotency_key,
    )
    if created:
        try:
            await queue.enqueue(
                "apply_import",
                {"run_id": str(run.id)},
                idempotency_key,
            )
        except Exception:
            await repository.delete_run(run.id)
            raise
    return JSONResponse(
        {"run_id": str(run.id)},
        status_code=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
    )


@router.get("/runs/{run_id}", response_model=RunRecord)
async def get_run(
    run_id: UUID,
    repository: Annotated[ImportRepositoryPort, Depends(get_import_repository)],
) -> RunRecord:
    run = await repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return run
