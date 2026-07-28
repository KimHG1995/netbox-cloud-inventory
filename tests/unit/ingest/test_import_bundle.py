import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cloud_inventory.domain.models import Completeness, DetailLevel, ResourceType
from cloud_inventory.ingest.parsers.base import SourceMetadata
from cloud_inventory.ingest.parsers.import_bundle import ImportBundleParser
from cloud_inventory.ingest.parsers.registry import build_default_registry


def aws_metadata(
    *,
    export_type: str = "auto",
    uploaded_at: datetime = datetime(2026, 7, 28, 0, 5, tzinfo=UTC),
    exported_at: datetime = datetime(2026, 7, 28, tzinfo=UTC),
) -> SourceMetadata:
    return SourceMetadata(
        provider="aws",
        realm="commercial",
        account_id="123456789012",
        export_type=export_type,
        uploaded_at=uploaded_at,
        exported_at=exported_at,
        region=None,
    )


def bundle_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "1",
        "provider": "aws",
        "realm": "commercial",
        "account_id": "123456789012",
        "exported_at": "2026-07-28T00:00:00Z",
        "resources": [],
    }
    document.update(overrides)
    return document


def write_bundle(path: Path, document: dict[str, object]) -> Path:
    path.write_text(json.dumps(document))
    return path


def vm_resource(**overrides: object) -> dict[str, object]:
    resource: dict[str, object] = {
        "schema_version": "1",
        "uid": "aws:commercial:123456789012:ap-northeast-2:virtual_machine:i-1",
        "provider": "aws",
        "realm": "commercial",
        "account_id": "123456789012",
        "region": "ap-northeast-2",
        "resource_type": "virtual_machine",
        "external_id": "i-1",
        "name": "vm-1",
        "observed_at": "2026-07-28T00:00:00Z",
    }
    resource.update(overrides)
    return resource


def test_registry_selects_import_bundle_by_content(tmp_path: Path) -> None:
    source = write_bundle(tmp_path / "renamed.data", bundle_document())

    parser = build_default_registry().detect(source, aws_metadata())

    assert parser.profile_id == "canonical.import_bundle.v1"


def test_explicit_profile_must_match_content(tmp_path: Path) -> None:
    source = tmp_path / "not-json.data"
    source.write_text("not json")

    with pytest.raises(ValueError, match="does not match"):
        build_default_registry().detect(
            source,
            aws_metadata(export_type="canonical.import_bundle.v1"),
        )


def test_source_metadata_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        aws_metadata(uploaded_at=datetime(2026, 7, 28))


def test_source_metadata_normalizes_timestamps_to_utc() -> None:
    metadata = aws_metadata()

    assert metadata.uploaded_at.tzinfo is UTC
    assert metadata.exported_at.tzinfo is UTC


def test_parser_rejects_provider_mismatch(tmp_path: Path) -> None:
    source = write_bundle(tmp_path / "bundle.json", bundle_document(provider="ncp"))

    with pytest.raises(ValueError, match="provider, realm, or account"):
        ImportBundleParser().parse(source, aws_metadata())


def test_parser_rejects_duplicate_uid(tmp_path: Path) -> None:
    resource = vm_resource()
    source = write_bundle(
        tmp_path / "bundle.json",
        bundle_document(resources=[resource, resource]),
    )

    with pytest.raises(ValueError, match="duplicate resource uid"):
        ImportBundleParser().parse(source, aws_metadata())


def test_parser_rejects_future_export_timestamp(tmp_path: Path) -> None:
    source = write_bundle(
        tmp_path / "bundle.json",
        bundle_document(exported_at="2026-07-28T00:11:00Z"),
    )

    with pytest.raises(ValueError, match="future"):
        ImportBundleParser().parse(
            source,
            aws_metadata(
                uploaded_at=datetime(2026, 7, 28, 0, 5, tzinfo=UTC),
                exported_at=datetime(2026, 7, 28, 0, 11, tzinfo=UTC),
            ),
        )


def test_parser_rejects_cloud_uid_mismatch(tmp_path: Path) -> None:
    source = write_bundle(
        tmp_path / "bundle.json",
        bundle_document(resources=[vm_resource(uid="wrong")]),
    )

    with pytest.raises(ValueError, match="cloud_uid"):
        ImportBundleParser().parse(source, aws_metadata())


def test_parser_materializes_account_and_records_unresolved_relation(
    tmp_path: Path,
) -> None:
    resource = vm_resource(
        relationships=[
            {
                "relation_type": "attached_to",
                "target_uid": "aws:commercial:123456789012:ap-northeast-2:subnet:missing",
            }
        ]
    )
    source = write_bundle(
        tmp_path / "bundle.json",
        bundle_document(resources=[resource]),
    )

    batch = ImportBundleParser().parse(source, aws_metadata())

    assert batch.completeness is Completeness.PARTIAL
    assert batch.parser_profiles == ["canonical.import_bundle.v1"]
    assert [resource.uid for resource in batch.resources] == sorted(
        resource.uid for resource in batch.resources
    )
    account = next(
        resource
        for resource in batch.resources
        if resource.resource_type is ResourceType.CLOUD_ACCOUNT
    )
    vm = next(
        resource
        for resource in batch.resources
        if resource.resource_type is ResourceType.VIRTUAL_MACHINE
    )
    assert account.external_id == "123456789012"
    assert vm.detail_level is DetailLevel.DETAILED
    assert vm.completeness is Completeness.PARTIAL
    assert vm.warnings == [
        "unresolved_relation:"
        "aws:commercial:123456789012:ap-northeast-2:subnet:missing"
    ]
    assert batch.warnings == vm.warnings


def test_complete_synthetic_fixture_parses() -> None:
    fixture = Path("tests/fixtures/import-bundle/full-inventory.json")

    batch = ImportBundleParser().parse(fixture, aws_metadata())

    present_types = {resource.resource_type for resource in batch.resources}
    assert present_types == set(ResourceType)
    assert len(batch.resources) == len(ResourceType)
