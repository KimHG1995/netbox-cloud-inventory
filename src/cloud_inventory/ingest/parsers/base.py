from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from cloud_inventory.domain.models import Provider, Realm, ResourceBatch


def _normalize_ingestion_timestamp(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class SourceMetadata:
    provider: Provider
    realm: Realm
    account_id: str
    export_type: str
    uploaded_at: datetime
    exported_at: datetime
    region: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", Provider(self.provider))
        object.__setattr__(self, "realm", Realm(self.realm))
        object.__setattr__(
            self,
            "uploaded_at",
            _normalize_ingestion_timestamp(self.uploaded_at, "uploaded_at"),
        )
        object.__setattr__(
            self,
            "exported_at",
            _normalize_ingestion_timestamp(self.exported_at, "exported_at"),
        )


@dataclass(frozen=True)
class DetectionResult:
    matched: bool
    confidence: int
    reason: str


class Parser(Protocol):
    profile_id: str
    schema_version: str

    def detect(self, path: Path, metadata: SourceMetadata) -> DetectionResult: ...

    def parse(self, path: Path, metadata: SourceMetadata) -> ResourceBatch: ...
