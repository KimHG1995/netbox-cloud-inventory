from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from cloud_inventory.persistence.base import Base


class SourceFileStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    PREVIEW_READY = "preview_ready"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"


class ArtifactStatus(StrEnum):
    AVAILABLE = "available"
    EXPIRED = "expired"


class PreviewStatus(StrEnum):
    READY = "ready"
    APPLYING = "applying"
    APPLIED = "applied"
    EXPIRED = "expired"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_class]


def _validated_priority(value: int) -> int:
    if not 0 <= value <= 1000:
        raise ValueError("mapping priority must be between 0 and 1000")
    return value


class ImportRequest(Base):
    __tablename__ = "import_request"
    __table_args__ = (
        UniqueConstraint("request_fingerprint"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(32))
    realm: Mapped[str] = mapped_column(String(32))
    account_id: Mapped[str] = mapped_column(String(255))
    export_type: Mapped[str] = mapped_column(String(255))
    region: Mapped[str | None] = mapped_column(String(255))
    exported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    created_by: Mapped[str] = mapped_column(String(255))


class SourceFile(Base):
    __tablename__ = "source_file"
    __table_args__ = (
        UniqueConstraint("deduplication_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    import_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_request.id", ondelete="CASCADE"),
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(1024))
    media_type: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    deduplication_key: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    artifact_key: Mapped[str] = mapped_column(String(2048))
    parser_profile: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[SourceFileStatus] = mapped_column(
        SqlEnum(
            SourceFileStatus,
            name="source_file_status",
            native_enum=False,
            values_callable=_enum_values,
        ),
        default=SourceFileStatus.UPLOADED,
    )
    artifact_status: Mapped[ArtifactStatus] = mapped_column(
        SqlEnum(
            ArtifactStatus,
            name="artifact_status",
            native_enum=False,
            values_callable=_enum_values,
        ),
        default=ArtifactStatus.AVAILABLE,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ImportPreview(Base):
    __tablename__ = "import_preview"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    import_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_request.id", ondelete="CASCADE"),
        index=True,
    )
    batch_hash: Mapped[str] = mapped_column(String(64))
    parser_versions: Mapped[dict[str, Any]] = mapped_column(JSON)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[PreviewStatus] = mapped_column(
        SqlEnum(
            PreviewStatus,
            name="preview_status",
            native_enum=False,
            values_callable=_enum_values,
        ),
        default=PreviewStatus.READY,
    )


class PreviewChange(Base):
    __tablename__ = "preview_change"
    __table_args__ = (
        UniqueConstraint(
            "preview_id",
            "cloud_uid",
            name="uq_preview_change_cloud_uid",
        ),
        UniqueConstraint(
            "preview_id",
            "ordinal",
            name="uq_preview_change_ordinal",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    preview_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_preview.id", ondelete="CASCADE"),
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    cloud_uid: Mapped[str] = mapped_column(String(2048))
    resource_type: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    changed_fields: Mapped[list[str]] = mapped_column(JSON)
    warning_codes: Mapped[list[str]] = mapped_column(JSON)
    desired: Mapped[dict[str, Any]] = mapped_column(JSON)


class CollectionJob(Base):
    __tablename__ = "collection_job"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_type: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[JobStatus] = mapped_column(
        SqlEnum(
            JobStatus,
            name="job_status",
            native_enum=False,
            values_callable=_enum_values,
        ),
        default=JobStatus.QUEUED,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )


class CollectionRun(Base):
    __tablename__ = "collection_run"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    import_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_request.id", ondelete="CASCADE"),
        index=True,
    )
    batch_hash: Mapped[str] = mapped_column(String(64))
    apply_valid_only: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[RunStatus] = mapped_column(
        SqlEnum(
            RunStatus,
            name="run_status",
            native_enum=False,
            values_callable=_enum_values,
        ),
        default=RunStatus.QUEUED,
    )
    checkpoint: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChangeSummary(Base):
    __tablename__ = "change_summary"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("collection_run.id", ondelete="CASCADE"),
        index=True,
    )
    cloud_uid: Mapped[str] = mapped_column(String(2048))
    action: Mapped[str] = mapped_column(String(64))
    changed_fields: Mapped[list[str]] = mapped_column(JSON)
    warning_codes: Mapped[list[str]] = mapped_column(JSON)


class TagMapping(Base):
    __tablename__ = "tag_mapping"
    __table_args__ = (
        CheckConstraint(
            "priority >= 0 AND priority <= 1000",
            name="priority_range",
        ),
        Index(
            "uq_tag_mapping_active",
            "provider",
            "realm",
            "account_id",
            "source_key_normalized",
            "source_value",
            unique=True,
            postgresql_where=text("enabled = true"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(32))
    realm: Mapped[str] = mapped_column(String(32))
    account_id: Mapped[str] = mapped_column(String(255))
    source_key: Mapped[str] = mapped_column(String(255))
    source_key_normalized: Mapped[str] = mapped_column(String(255))
    source_value: Mapped[str] = mapped_column(String(1024))
    business_service_code: Mapped[str] = mapped_column(String(255))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    @validates("source_key")
    def validate_source_key(self, _key: str, value: str) -> str:
        self.source_key_normalized = value.strip().casefold()
        return value

    @validates("source_value")
    def validate_source_value(self, _key: str, value: str) -> str:
        return value.strip()

    @validates("priority")
    def validate_priority(self, _key: str, value: int) -> int:
        return _validated_priority(value)


class OwnerMapping(Base):
    __tablename__ = "owner_mapping"
    __table_args__ = (
        CheckConstraint(
            "priority >= 0 AND priority <= 1000",
            name="priority_range",
        ),
        Index(
            "uq_owner_mapping_active",
            "provider",
            "realm",
            "account_id",
            "source_value",
            unique=True,
            postgresql_where=text("enabled = true"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(32))
    realm: Mapped[str] = mapped_column(String(32))
    account_id: Mapped[str] = mapped_column(String(255))
    source_value: Mapped[str] = mapped_column(String(1024))
    netbox_owner_id: Mapped[int] = mapped_column(BigInteger)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    @validates("source_value")
    def validate_source_value(self, _key: str, value: str) -> str:
        return value.strip()

    @validates("priority")
    def validate_priority(self, _key: str, value: int) -> int:
        return _validated_priority(value)
