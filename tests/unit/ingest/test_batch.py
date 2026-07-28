from datetime import UTC, datetime, timedelta

import pytest

from cloud_inventory.domain.models import (
    CloudResource,
    DetailLevel,
    Provider,
    Realm,
    ResourceType,
)
from cloud_inventory.domain.uid import build_cloud_uid
from cloud_inventory.ingest.batch import combine_batches, finalize_batch

ACCOUNT_ID = "123456789012"
OBSERVED_AT = datetime(2026, 7, 28, tzinfo=UTC)


def cloud_resource(
    *,
    name: str,
    observed_at: datetime = OBSERVED_AT,
    source_profile: str = "test.profile",
    source_priority: int = 100,
    attributes: dict[str, object] | None = None,
    tags: dict[str, str] | None = None,
) -> CloudResource:
    return CloudResource(
        uid=build_cloud_uid(
            Provider.AWS,
            Realm.COMMERCIAL,
            ACCOUNT_ID,
            "ap-northeast-2",
            ResourceType.VIRTUAL_MACHINE,
            "i-1",
        ),
        provider=Provider.AWS,
        realm=Realm.COMMERCIAL,
        account_id=ACCOUNT_ID,
        region="ap-northeast-2",
        resource_type=ResourceType.VIRTUAL_MACHINE,
        external_id="i-1",
        name=name,
        attributes=attributes or {},
        tags=tags or {},
        observed_at=observed_at,
        completeness="partial",
        detail_level=DetailLevel.SUMMARY,
        source_profile=source_profile,
        source_priority=source_priority,
    )


def batch_for(resource: CloudResource):
    return finalize_batch(
        provider=Provider.AWS,
        realm=Realm.COMMERCIAL,
        account_id=ACCOUNT_ID,
        observed_at=resource.observed_at,
        resources=[resource],
        parser_profile=resource.source_profile,
        source_priority=resource.source_priority,
        detail_level=resource.detail_level,
    )


def test_combine_batches_is_independent_of_file_order() -> None:
    first = batch_for(cloud_resource(name="a", source_profile="a.profile"))
    second = batch_for(cloud_resource(name="z", source_profile="z.profile"))

    forward = combine_batches([first, second])
    reversed_result = combine_batches([second, first])

    assert forward == reversed_result
    vm = next(
        resource
        for resource in forward.resources
        if resource.resource_type is ResourceType.VIRTUAL_MACHINE
    )
    assert vm.name == "z"


def test_newer_empty_values_do_not_erase_non_empty_values() -> None:
    older = batch_for(
        cloud_resource(
            name="poc-web-01",
            attributes={"service": "portal"},
            tags={"Environment": "test"},
        )
    )
    newer = batch_for(
        cloud_resource(
            name="unknown",
            observed_at=OBSERVED_AT + timedelta(minutes=1),
            attributes={"service": ""},
            tags={"Environment": ""},
        )
    )

    merged = combine_batches([older, newer])
    vm = next(
        resource
        for resource in merged.resources
        if resource.resource_type is ResourceType.VIRTUAL_MACHINE
    )

    assert vm.name == "poc-web-01"
    assert vm.attributes["service"] == "portal"
    assert vm.tags["Environment"] == "test"
    assert vm.observed_at == OBSERVED_AT + timedelta(minutes=1)


def test_equal_profile_conflict_uses_canonical_hash_tie_break() -> None:
    left = batch_for(cloud_resource(name="left"))
    right = batch_for(cloud_resource(name="right"))

    assert combine_batches([left, right]) == combine_batches([right, left])


def test_combine_batches_requires_one_inventory_identity() -> None:
    batch = batch_for(cloud_resource(name="vm"))
    different = batch.model_copy(update={"account_id": "999999999999"})

    with pytest.raises(ValueError, match="provider, realm, and account"):
        combine_batches([batch, different])
