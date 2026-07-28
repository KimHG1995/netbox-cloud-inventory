import json
from datetime import timedelta
from pathlib import Path

from cloud_inventory.domain.models import (
    CloudResource,
    Completeness,
    DetailLevel,
    ImportBundle,
    ResourceBatch,
)
from cloud_inventory.domain.uid import build_cloud_uid
from cloud_inventory.ingest.batch import finalize_batch
from cloud_inventory.ingest.parsers.base import DetectionResult, SourceMetadata


class ImportBundleParser:
    profile_id = "canonical.import_bundle.v1"
    schema_version = "1"
    source_priority = 300

    def detect(self, path: Path, metadata: SourceMetadata) -> DetectionResult:
        del metadata
        try:
            document = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return DetectionResult(False, 0, "content is not UTF-8 JSON")

        required_keys = {
            "schema_version",
            "provider",
            "realm",
            "account_id",
            "exported_at",
            "resources",
        }
        matched = (
            isinstance(document, dict)
            and document.get("schema_version") == self.schema_version
            and required_keys.issubset(document)
        )
        return DetectionResult(
            matched=matched,
            confidence=100 if matched else 0,
            reason="canonical Import Bundle keys matched"
            if matched
            else "canonical Import Bundle keys are missing",
        )

    def parse(self, path: Path, metadata: SourceMetadata) -> ResourceBatch:
        try:
            raw = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Import Bundle must be UTF-8 JSON") from error

        bundle = ImportBundle.model_validate_json(raw)
        if (bundle.provider, bundle.realm, bundle.account_id) != (
            metadata.provider,
            metadata.realm,
            metadata.account_id,
        ):
            raise ValueError("Import Bundle provider, realm, or account mismatch")

        exported_at = bundle.exported_at.astimezone(metadata.exported_at.tzinfo)
        if exported_at != metadata.exported_at:
            raise ValueError("Import Bundle exported_at does not match upload metadata")
        if exported_at > metadata.uploaded_at + timedelta(minutes=5):
            raise ValueError("Import Bundle exported_at is more than five minutes in the future")

        resources: list[CloudResource] = []
        for resource in bundle.resources:
            expected_uid = build_cloud_uid(
                resource.provider,
                resource.realm,
                resource.account_id,
                resource.region,
                resource.resource_type,
                resource.external_id,
            )
            if resource.uid != expected_uid:
                raise ValueError(f"cloud_uid mismatch: {resource.uid}")

            resources.append(
                CloudResource.model_validate(
                    {
                        **resource.model_dump(),
                        "source": "export",
                        "completeness": Completeness.PARTIAL,
                        "detail_level": DetailLevel.DETAILED,
                        "source_profile": self.profile_id,
                        "source_priority": self.source_priority,
                    }
                )
            )

        return finalize_batch(
            provider=metadata.provider,
            realm=metadata.realm,
            account_id=metadata.account_id,
            observed_at=bundle.exported_at,
            resources=resources,
            parser_profile=self.profile_id,
            source_priority=self.source_priority,
            detail_level=DetailLevel.DETAILED,
        )
