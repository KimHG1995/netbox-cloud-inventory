from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

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
from cloud_inventory.domain.uid import build_cloud_uid
from cloud_inventory.netbox.client import NetBoxObject, PreconditionFailedError
from cloud_inventory.netbox.writer import NetBoxWriter
from cloud_inventory.persistence.models import OwnerMapping, TagMapping
from cloud_inventory.reconciliation.diff import Reconciler


def resource(
    resource_type: ResourceType,
    external_id: str,
    *,
    name: str,
    status: str = "active",
    attributes: dict[str, object] | None = None,
    relationships: list[dict[str, str]] | None = None,
) -> CloudResource:
    region = "global" if resource_type is ResourceType.CLOUD_ACCOUNT else "ap-northeast-2"
    return CloudResource.model_validate(
        {
            "uid": build_cloud_uid(
                Provider.AWS,
                Realm.COMMERCIAL,
                "111111111111",
                region,
                resource_type,
                external_id,
            ),
            "provider": Provider.AWS,
            "realm": Realm.COMMERCIAL,
            "account_id": "111111111111",
            "region": region,
            "resource_type": resource_type,
            "external_id": external_id,
            "name": name,
            "status": status,
            "attributes": attributes or {},
            "tags": (
                {}
                if resource_type is ResourceType.CLOUD_ACCOUNT
                else {"Service": "payments", "Owner": "platform"}
            ),
            "relationships": relationships or [],
            "observed_at": datetime(2026, 7, 28, 1, 2, tzinfo=UTC),
            "completeness": Completeness.PARTIAL,
            "detail_level": DetailLevel.SUMMARY,
            "source_profile": "test.v1",
            "source_priority": 100,
        }
    )


def preview_for(*resources: CloudResource):
    batch = ResourceBatch(
        batch_id=uuid4(),
        provider=Provider.AWS,
        realm=Realm.COMMERCIAL,
        account_id="111111111111",
        observed_at=datetime(2026, 7, 28, 1, 2, tzinfo=UTC),
        completeness=Completeness.PARTIAL,
        scopes=[
            ResourceScope(
                region="ap-northeast-2",
                resource_type=item.resource_type,
                completeness=Completeness.PARTIAL,
            )
            for item in resources
        ],
        resources=list(resources),
        parser_profiles=["test.v1"],
        content_hash="batch-hash",
    )
    return Reconciler().preview(batch, {})


class FakeClient:
    def __init__(self) -> None:
        self.objects: dict[str, NetBoxObject] = {}
        self.calls: list[tuple[str, ResourceType, dict[str, Any]]] = []
        self.fail_once: set[str] = set()
        self.next_id = 1
        self.business_services: dict[str, dict[str, Any]] = {}
        self.owners: dict[int, dict[str, Any]] = {}
        self.service_patches: list[tuple[int, list[dict[str, Any]]]] = []
        self.owner_lookups: list[int] = []
        self.vm_interfaces: dict[str, NetBoxObject] = {}
        self.vm_interface_calls: list[tuple[str, dict[str, Any]]] = []

    async def get_by_cloud_uid(
        self,
        resource_type: ResourceType,
        cloud_uid: str,
    ) -> NetBoxObject | None:
        return self.objects.get(cloud_uid)

    async def create(
        self,
        resource_type: ResourceType,
        payload: dict[str, Any],
    ) -> NetBoxObject:
        self.calls.append(("create", resource_type, payload))
        cloud_uid = str(payload.get("cloud_uid") or payload["custom_fields"]["cloud_uid"])
        result = NetBoxObject(
            id=self.next_id,
            resource_type=resource_type,
            data={"id": self.next_id, **payload},
            etag=f'"{self.next_id}"',
        )
        self.next_id += 1
        self.objects[cloud_uid] = result
        return result

    async def update(
        self,
        resource_type: ResourceType,
        object_id: int,
        payload: dict[str, Any],
        etag: str,
    ) -> NetBoxObject:
        self.calls.append(("update", resource_type, payload))
        cloud_uid_value = payload.get("cloud_uid")
        custom_fields = payload.get("custom_fields")
        if cloud_uid_value is None and isinstance(custom_fields, dict):
            cloud_uid_value = custom_fields.get("cloud_uid")
        if cloud_uid_value is None:
            current_uid, current = next(
                (uid, item)
                for uid, item in self.objects.items()
                if item.id == object_id
            )
            cloud_uid = current_uid
            merged = {**current.data, **payload}
        else:
            cloud_uid = str(cloud_uid_value)
            current = self.objects.get(cloud_uid)
            merged = {
                **(current.data if current is not None else {"id": object_id}),
                **payload,
            }
        if cloud_uid in self.fail_once:
            self.fail_once.remove(cloud_uid)
            raise PreconditionFailedError("precondition failed")
        result = NetBoxObject(
            id=object_id,
            resource_type=resource_type,
            data=merged,
            etag='"updated"',
        )
        self.objects[cloud_uid] = result
        return result

    async def get_business_service(self, service_code: str) -> dict[str, Any] | None:
        return self.business_services.get(service_code)

    async def get_owner(self, owner_id: int) -> dict[str, Any] | None:
        self.owner_lookups.append(owner_id)
        return self.owners.get(owner_id)

    async def patch_business_service_resources(
        self,
        service_id: int,
        resources: list[dict[str, Any]],
    ) -> None:
        self.service_patches.append((service_id, resources))

    async def get_vm_interface_by_cloud_uid(
        self,
        cloud_uid: str,
    ) -> NetBoxObject | None:
        return self.vm_interfaces.get(cloud_uid)

    async def create_vm_interface(
        self,
        payload: dict[str, Any],
    ) -> NetBoxObject:
        self.vm_interface_calls.append(("create", payload))
        cloud_uid = str(payload["custom_fields"]["cloud_uid"])
        result = NetBoxObject(
            id=self.next_id,
            resource_type=ResourceType.NETWORK_INTERFACE,
            data={"id": self.next_id, **payload},
            etag=f'"vm-{self.next_id}"',
        )
        self.next_id += 1
        self.vm_interfaces[cloud_uid] = result
        return result

    async def update_vm_interface(
        self,
        object_id: int,
        payload: dict[str, Any],
        etag: str,
    ) -> NetBoxObject:
        self.vm_interface_calls.append(("update", payload))
        cloud_uid = str(payload["custom_fields"]["cloud_uid"])
        result = NetBoxObject(
            id=object_id,
            resource_type=ResourceType.NETWORK_INTERFACE,
            data={"id": object_id, **payload},
            etag='"vm-updated"',
        )
        self.vm_interfaces[cloud_uid] = result
        return result


@pytest.mark.asyncio
async def test_writer_orders_dependency_stages_and_saves_checkpoints() -> None:
    account = resource(
        ResourceType.CLOUD_ACCOUNT,
        "111111111111",
        name="main",
    )
    region = resource(
        ResourceType.REGION,
        "ap-northeast-2",
        name="Seoul",
    )
    vpc = resource(ResourceType.VPC, "vpc-1", name="production")
    database = resource(
        ResourceType.MANAGED_DATABASE,
        "db-1",
        name="orders",
        attributes={"engine": "postgresql"},
    )
    checkpoints: list[int] = []
    client = FakeClient()

    result = await NetBoxWriter(client, checkpoint=checkpoints.append).apply(
        preview_for(database, vpc, region, account)
    )

    assert [call[1] for call in client.calls] == [
        ResourceType.CLOUD_ACCOUNT,
        ResourceType.REGION,
        ResourceType.VPC,
        ResourceType.MANAGED_DATABASE,
    ]
    assert checkpoints == list(range(1, 10))
    assert result.created == 4
    assert result.failed == 0


@pytest.mark.asyncio
async def test_writer_uses_only_collector_managed_fields() -> None:
    account = resource(
        ResourceType.CLOUD_ACCOUNT,
        "111111111111",
        name="main",
    )
    bucket = resource(
        ResourceType.OBJECT_BUCKET,
        "bucket-1",
        name="assets",
        attributes={
            "versioning": True,
            "description": "human text",
            "comments": "human comment",
            "owner": "unapproved",
            "runbook_url": "https://example.test/runbook",
            "repository_url": "https://example.test/repository",
        },
    )
    client = FakeClient()

    await NetBoxWriter(client).apply(preview_for(bucket, account))

    payload = client.calls[1][2]
    assert payload["source_attributes"] == {"versioning": True}
    assert "description" not in payload
    assert "comments" not in payload
    assert "owner" not in payload
    assert "runbook_url" not in payload
    assert "repository_url" not in payload


@pytest.mark.asyncio
async def test_unmaterializable_core_resource_is_retained_as_warning() -> None:
    subnet = resource(
        ResourceType.SUBNET,
        "subnet-1",
        name="missing-cidr",
    )
    client = FakeClient()

    result = await NetBoxWriter(client).apply(preview_for(subnet))

    assert client.calls == []
    assert result.warnings == 1
    assert result.changes[0].warning_codes == ["unmaterializable_summary"]


@pytest.mark.asyncio
async def test_writer_refetches_and_reconciles_once_after_412() -> None:
    vpc = resource(ResourceType.VPC, "vpc-1", name="production")
    client = FakeClient()
    current = NetBoxObject(
        id=7,
        resource_type=ResourceType.VPC,
        data={
            "id": 7,
            "name": "old",
            "custom_fields": {
                "cloud_uid": vpc.uid,
                "provider": "aws",
                "realm": "commercial",
                "account_id": "111111111111",
            },
        },
        etag='"one"',
    )
    client.objects[vpc.uid] = current
    client.fail_once.add(vpc.uid)

    result = await NetBoxWriter(client).apply(preview_for(vpc))

    assert [call[0] for call in client.calls] == ["update", "update"]
    assert result.updated == 1
    assert result.failed == 0


@pytest.mark.asyncio
async def test_approved_mappings_attach_service_and_empty_owner() -> None:
    account = resource(
        ResourceType.CLOUD_ACCOUNT,
        "111111111111",
        name="main",
    )
    bucket = resource(ResourceType.OBJECT_BUCKET, "bucket-1", name="assets")
    client = FakeClient()
    client.business_services["payments"] = {
        "id": 90,
        "service_code": "payments",
        "resources": [{"object_type": "ipam/vrf", "id": 3}],
    }
    client.owners[70] = {"id": 70, "name": "platform"}
    tag_mapping = TagMapping(
        id=uuid4(),
        provider="aws",
        realm="commercial",
        account_id="111111111111",
        source_key="Service",
        source_value="payments",
        business_service_code="payments",
        priority=100,
        enabled=True,
    )
    owner_mapping = OwnerMapping(
        id=uuid4(),
        provider="aws",
        realm="commercial",
        account_id="111111111111",
        source_value="platform",
        netbox_owner_id=70,
        priority=100,
        enabled=True,
    )

    result = await NetBoxWriter(
        client,
        tag_mappings=[tag_mapping],
        owner_mappings=[owner_mapping],
    ).apply(preview_for(bucket, account))

    bucket_object = client.objects[bucket.uid]
    assert bucket_object.data["owner"] == 70
    assert client.service_patches == [
        (
            90,
            [
                {"object_type": "ipam/vrf", "id": 3},
                {"object_type": "custom-objects/object-bucket", "id": bucket_object.id},
            ],
        )
    ]
    bucket_change = next(item for item in result.changes if item.cloud_uid == bucket.uid)
    assert bucket_change.warning_codes == []


@pytest.mark.asyncio
async def test_disabled_mapping_is_ignored_and_missing_owner_is_unresolved() -> None:
    account = resource(
        ResourceType.CLOUD_ACCOUNT,
        "111111111111",
        name="main",
    )
    bucket = resource(ResourceType.OBJECT_BUCKET, "bucket-1", name="assets")
    disabled_service = TagMapping(
        id=uuid4(),
        provider="aws",
        realm="commercial",
        account_id="111111111111",
        source_key="Service",
        source_value="payments",
        business_service_code="payments",
        priority=100,
        enabled=False,
    )
    missing_owner = OwnerMapping(
        id=uuid4(),
        provider="aws",
        realm="commercial",
        account_id="111111111111",
        source_value="platform",
        netbox_owner_id=404,
        priority=100,
        enabled=True,
    )
    client = FakeClient()

    result = await NetBoxWriter(
        client,
        tag_mappings=[disabled_service],
        owner_mappings=[missing_owner],
    ).apply(preview_for(bucket, account))

    assert client.service_patches == []
    assert client.owner_lookups == [404]
    bucket_change = next(item for item in result.changes if item.cloud_uid == bucket.uid)
    assert "unresolved_owner" in bucket_change.warning_codes


@pytest.mark.asyncio
async def test_network_interface_creates_native_vm_interface_when_vm_resolves() -> None:
    account = resource(
        ResourceType.CLOUD_ACCOUNT,
        "111111111111",
        name="main",
    )
    virtual_machine = resource(
        ResourceType.VIRTUAL_MACHINE,
        "i-123",
        name="app-01",
    )
    network_interface = resource(
        ResourceType.NETWORK_INTERFACE,
        "eni-123",
        name="eth0",
        relationships=[
            {
                "relation_type": "attached_to",
                "target_uid": virtual_machine.uid,
            }
        ],
    )
    client = FakeClient()

    result = await NetBoxWriter(client).apply(
        preview_for(network_interface, virtual_machine, account)
    )

    assert [call[0] for call in client.vm_interface_calls] == ["create"]
    cloud_interface = client.objects[network_interface.uid]
    assert cloud_interface.data["attached_virtual_machine"] == client.objects[
        virtual_machine.uid
    ].id
    assert cloud_interface.data["vm_interface"] == next(
        iter(client.vm_interfaces.values())
    ).id
    assert result.failed == 0


@pytest.mark.asyncio
async def test_approved_owner_mapping_preserves_different_manual_owner() -> None:
    account = resource(
        ResourceType.CLOUD_ACCOUNT,
        "111111111111",
        name="main",
    )
    bucket = resource(ResourceType.OBJECT_BUCKET, "bucket-1", name="assets")
    client = FakeClient()
    client.owners[70] = {"id": 70, "name": "platform"}
    client.objects[bucket.uid] = NetBoxObject(
        id=50,
        resource_type=ResourceType.OBJECT_BUCKET,
        data={"id": 50, "cloud_uid": bucket.uid, "owner": {"id": 999}},
        etag='"manual-owner"',
    )
    mapping = OwnerMapping(
        id=uuid4(),
        provider="aws",
        realm="commercial",
        account_id="111111111111",
        source_value="platform",
        netbox_owner_id=70,
        priority=100,
        enabled=True,
    )

    result = await NetBoxWriter(
        client,
        owner_mappings=[mapping],
    ).apply(preview_for(bucket, account))

    assert client.objects[bucket.uid].data["owner"] == {"id": 999}
    owner_updates = [
        payload
        for action, resource_type, payload in client.calls
        if action == "update"
        and resource_type is ResourceType.OBJECT_BUCKET
        and set(payload) == {"owner"}
    ]
    assert owner_updates == []
    bucket_change = next(item for item in result.changes if item.cloud_uid == bucket.uid)
    assert "owner_conflict" in bucket_change.warning_codes
