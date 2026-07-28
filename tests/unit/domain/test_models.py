import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from cloud_inventory.domain.models import CloudResource, ImportBundle, ImportResource


def import_resource(**overrides: object) -> ImportResource:
    values: dict[str, object] = {
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
    values.update(overrides)
    return ImportResource.model_validate(values)


def test_export_resource_cannot_claim_full_completeness() -> None:
    with pytest.raises(ValidationError, match="partial"):
        CloudResource(
            uid="x",
            provider="aws",
            realm="commercial",
            account_id="123456789012",
            region="ap-northeast-2",
            resource_type="virtual_machine",
            external_id="i-1",
            name="vm-1",
            source="export",
            completeness="full",
            detail_level="summary",
            source_profile="test.export.v1",
            source_priority=1,
            observed_at="2026-07-28T00:00:00Z",
        )


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "password",
        "Pass_Word",
        "passwd",
        "secret",
        "token",
        "access-key",
        "secretKey",
        "private key",
        "credential",
        "connection.string",
    ],
)
def test_nested_sensitive_attribute_keys_are_rejected(sensitive_key: str) -> None:
    with pytest.raises(ValidationError, match="sensitive"):
        import_resource(attributes={"safe": [{"nested": {sensitive_key: "not-inspected"}}]})


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "password",
        "passwd",
        "secret",
        "token",
        "accesskey",
        "secretkey",
        "privatekey",
        "credential",
        "connectionstring",
    ],
)
def test_sensitive_tag_keys_are_rejected(sensitive_key: str) -> None:
    with pytest.raises(ValidationError, match="sensitive"):
        import_resource(tags={sensitive_key: "not-inspected"})


def test_ordinary_values_are_not_scanned_for_secret_words() -> None:
    resource = import_resource(
        attributes={"description": "token rotation service"},
        tags={"purpose": "secret inventory"},
    )

    assert resource.attributes["description"] == "token rotation service"


def test_import_resource_forbids_internal_parser_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        import_resource(source="export")


def test_bundle_requires_matching_resource_identity() -> None:
    with pytest.raises(ValidationError, match="provider, realm, and account"):
        ImportBundle(
            schema_version="1",
            provider="ncp",
            realm="commercial",
            account_id="123456789012",
            exported_at="2026-07-28T00:00:00Z",
            resources=[import_resource()],
        )


def test_bundle_records_unresolved_relationship_warning() -> None:
    resource = import_resource(
        relationships=[
            {
                "relation_type": "attached_to",
                "target_uid": "aws:commercial:123456789012:ap-northeast-2:vpc:vpc-404",
            }
        ]
    )

    bundle = ImportBundle(
        schema_version="1",
        provider="aws",
        realm="commercial",
        account_id="123456789012",
        exported_at="2026-07-28T00:00:00Z",
        resources=[resource],
    )

    assert bundle.resources[0].warnings == [
        "unresolved_relation:"
        "aws:commercial:123456789012:ap-northeast-2:vpc:vpc-404"
    ]


def test_import_bundle_schema_matches_committed_artifact() -> None:
    schema_path = Path("schemas/import-bundle-v1.schema.json")
    expected = json.dumps(
        ImportBundle.model_json_schema(mode="validation"),
        indent=2,
        sort_keys=True,
    ) + "\n"

    assert schema_path.read_text() == expected


def test_aware_timestamps_are_required() -> None:
    with pytest.raises(ValidationError):
        import_resource(observed_at=datetime(2026, 7, 28))

    resource = import_resource(observed_at=datetime(2026, 7, 28, tzinfo=UTC))
    assert resource.observed_at.tzinfo is UTC
