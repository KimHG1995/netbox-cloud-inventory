from datetime import UTC, datetime
from uuid import uuid4

from cloud_inventory.domain.models import (
    CloudResource,
    Completeness,
    DetailLevel,
    Provider,
    Realm,
    ResourceBatch,
    ResourceScope,
    ResourceType,
)
from cloud_inventory.reconciliation.diff import ChangeAction, Reconciler
from cloud_inventory.reconciliation.fingerprint import compute_fingerprint


def resource(
    uid: str = "aws:commercial:111111111111:virtual_machine:i-123",
    *,
    name: str = "app-01",
    status: str = "active",
    attributes: dict[str, object] | None = None,
    tags: dict[str, str] | None = None,
    relationships: list[dict[str, str]] | None = None,
) -> CloudResource:
    return CloudResource.model_validate(
        {
            "uid": uid,
            "provider": Provider.AWS,
            "realm": Realm.COMMERCIAL,
            "account_id": "111111111111",
            "region": "ap-northeast-2",
            "resource_type": ResourceType.VIRTUAL_MACHINE,
            "external_id": "i-123",
            "name": name,
            "status": status,
            "attributes": attributes or {"instance_type": "t3.small"},
            "tags": tags or {"Environment": "dev", "Service": "payments"},
            "relationships": (
                relationships
                if relationships is not None
                else [
                    {
                        "relation_type": "attached_to",
                        "target_uid": (
                            "aws:commercial:111111111111:ap-northeast-2:"
                            "zone:ap-northeast-2a"
                        ),
                    }
                ]
            ),
            "observed_at": datetime(2026, 7, 28, 1, 2, tzinfo=UTC),
            "completeness": Completeness.PARTIAL,
            "detail_level": DetailLevel.SUMMARY,
            "source_profile": "aws.resource-explorer.v1",
            "source_priority": 100,
        }
    )


def batch(*resources: CloudResource, content_hash: str = "batch-hash") -> ResourceBatch:
    return ResourceBatch(
        batch_id=uuid4(),
        provider=Provider.AWS,
        realm=Realm.COMMERCIAL,
        account_id="111111111111",
        observed_at=datetime(2026, 7, 28, 1, 2, tzinfo=UTC),
        completeness=Completeness.PARTIAL,
        scopes=[
            ResourceScope(
                region="ap-northeast-2",
                resource_type=ResourceType.VIRTUAL_MACHINE,
                completeness=Completeness.PARTIAL,
            )
        ],
        resources=list(resources),
        parser_profiles=["aws.resource-explorer.v1"],
        content_hash=content_hash,
    )


def test_fingerprint_is_independent_of_mapping_order() -> None:
    first = resource(
        attributes={"network": {"b": 2, "a": 1}, "ports": [443, 80]},
        tags={"Service": "payments", "Environment": "dev"},
    )
    second = resource(
        attributes={"ports": [443, 80], "network": {"a": 1, "b": 2}},
        tags={"Environment": "dev", "Service": "payments"},
    )

    assert compute_fingerprint(first) == compute_fingerprint(second)


def test_fingerprint_excludes_user_managed_fields() -> None:
    first = resource(
        attributes={
            "instance_type": "t3.small",
            "description": "owned by a human",
            "comments": "keep this",
            "owner": "platform",
            "runbook_url": "https://example.test/one",
        }
    )
    second = resource(
        attributes={
            "instance_type": "t3.small",
            "description": "changed",
            "comments": "changed",
            "owner": "different",
            "runbook_url": "https://example.test/two",
        }
    )

    assert compute_fingerprint(first) == compute_fingerprint(second)


def test_preview_preserves_empty_and_unknown_incoming_values() -> None:
    current = resource(
        name="current-name",
        status="active",
        attributes={"instance_type": "t3.large", "platform": "linux"},
    )
    incoming = resource(
        name="",
        status="unknown",
        attributes={"instance_type": "", "platform": "linux"},
    )

    preview = Reconciler().preview(batch(incoming), {current.uid: current})

    assert preview.unchanged == 1
    assert preview.changes[0].action is ChangeAction.UNCHANGED
    assert preview.changes[0].desired.name == "current-name"
    assert preview.changes[0].desired.status == "active"
    assert preview.changes[0].desired.attributes["instance_type"] == "t3.large"


def test_preview_classifies_create_update_and_unchanged() -> None:
    unchanged = resource()
    changed = resource(
        "aws:commercial:111111111111:virtual_machine:i-456",
        name="worker",
        status="stopped",
    )
    current_changed = changed.model_copy(update={"status": "active"})
    created = resource(
        "aws:commercial:111111111111:virtual_machine:i-789",
        name="new",
    )

    preview = Reconciler().preview(
        batch(unchanged, changed, created),
        {
            unchanged.uid: unchanged,
            current_changed.uid: current_changed,
        },
    )

    assert (preview.created, preview.updated, preview.unchanged) == (1, 1, 1)
    actions = {change.cloud_uid: change.action for change in preview.changes}
    assert actions[created.uid] is ChangeAction.CREATE
    assert actions[changed.uid] is ChangeAction.UPDATE
    assert actions[unchanged.uid] is ChangeAction.UNCHANGED
    assert preview.changes[1].changed_fields == ["status"]


def test_manual_partial_batch_never_deletes_absent_current_objects() -> None:
    incoming = resource()
    absent_from_batch = resource(
        "aws:commercial:111111111111:virtual_machine:i-old",
        name="old",
    )

    preview = Reconciler().preview(
        batch(incoming),
        {incoming.uid: incoming, absent_from_batch.uid: absent_from_batch},
    )

    assert len(preview.changes) == 1
    assert {change.action for change in preview.changes} == {ChangeAction.UNCHANGED}
    assert all(change.action.value not in {"delete", "inactive"} for change in preview.changes)


def test_repeating_same_batch_is_unchanged() -> None:
    incoming = resource()
    first = Reconciler().preview(batch(incoming), {})
    current = {change.cloud_uid: change.desired for change in first.changes}

    second = Reconciler().preview(batch(incoming), current)

    assert second.created == 0
    assert second.updated == 0
    assert second.unchanged == 1


def test_summary_vm_without_zone_is_a_preview_warning() -> None:
    incoming = resource(relationships=[])

    preview = Reconciler().preview(batch(incoming), {})

    assert preview.created == 0
    assert preview.warnings == 1
    assert preview.changes[0].action is ChangeAction.WARNING
    assert preview.changes[0].warnings == ["unmaterializable_summary"]
