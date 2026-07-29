import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from cloud_inventory.netbox.bootstrap import (
    HttpNetBoxBootstrapClient,
    apply_custom_object_schema,
    bootstrap_core_fields,
)

SCHEMA_PATH = Path("schemas/netbox/custom-objects-v1.json")


class FakeBootstrapClient:
    def __init__(self) -> None:
        self.objects: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.next_id = 1
        self.schema_preview: dict[str, Any] = {"diffs": []}

    async def list(
        self,
        endpoint: str,
        params: dict[str, str],
    ) -> list[dict[str, Any]]:
        return [
            item
            for item in self.objects[endpoint]
            if all(item.get(key) == value for key, value in params.items())
        ]

    async def create(
        self,
        endpoint: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        item = {"id": self.next_id, **payload}
        self.next_id += 1
        self.objects[endpoint].append(item)
        return item

    async def patch(
        self,
        endpoint: str,
        object_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        item = next(
            item for item in self.objects[endpoint] if item["id"] == object_id
        )
        item.update(payload)
        return item

    async def post(
        self,
        endpoint: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if endpoint.endswith("/preview/"):
            return self.schema_preview
        return {"applied": True, "diffs": self.schema_preview["diffs"]}


def test_portable_schema_has_stable_order_and_common_fields() -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    type_definitions = schema["types"]

    assert [item["name"] for item in type_definitions] == [
        "cloud_account",
        "cloud_network_interface",
        "managed_database",
        "object_bucket",
        "cloud_load_balancer",
        "domain",
        "dns_zone",
        "dns_record",
        "business_service",
    ]
    expected_common = [
        "name",
        "cloud_uid",
        "external_id",
        "provider",
        "realm",
        "account_id",
        "region_name",
        "status",
        "last_seen_at",
        "sync_state",
        "source_tags",
        "source_attributes",
    ]
    for type_definition in type_definitions[:-1]:
        assert [field["id"] for field in type_definition["fields"][:12]] == list(
            range(1, 13)
        )
        assert [
            field["name"] for field in type_definition["fields"][:12]
        ] == expected_common


def test_polymorphic_fields_have_exact_allowed_types() -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    fields = {
        (type_definition["slug"], field["name"]): field
        for type_definition in schema["types"]
        for field in type_definition["fields"]
    }

    backend_resources = fields[("cloud-load-balancer", "backend_resources")]
    related_resources = fields[("dns-record", "related_resources")]
    service_resources = fields[("business-service", "resources")]
    assert backend_resources["is_polymorphic"] is True
    assert backend_resources["related_object_types"] == [
        "virtualization/virtualmachine",
        "ipam/ipaddress",
        "custom-objects/managed-database",
        "custom-objects/object-bucket",
    ]
    assert related_resources["is_polymorphic"] is True
    assert len(related_resources["related_object_types"]) == 5
    assert service_resources["is_polymorphic"] is True
    assert len(service_resources["related_object_types"]) == 15


@pytest.mark.asyncio
async def test_core_field_bootstrap_is_idempotent() -> None:
    client = FakeBootstrapClient()

    first = await bootstrap_core_fields(client)
    second = await bootstrap_core_fields(client)

    assert first.created == 15
    assert first.changed == 0
    assert second.created == 0
    assert second.changed == 0
    assert second.unchanged == 15


@pytest.mark.asyncio
async def test_schema_apply_rejects_destructive_preview() -> None:
    client = FakeBootstrapClient()
    client.schema_preview = {
        "diffs": [
            {
                "slug": "cloud_account",
                "is_new": False,
                "has_changes": True,
                "has_destructive_changes": True,
            }
        ]
    }

    with pytest.raises(ValueError, match="destructive"):
        await apply_custom_object_schema(
            client,
            json.loads(SCHEMA_PATH.read_text()),
        )


@pytest.mark.asyncio
async def test_live_netbox_schema_is_idempotent() -> None:
    if os.getenv("RUN_NETBOX_INTEGRATION") != "1":
        pytest.skip("set RUN_NETBOX_INTEGRATION=1 for the live NetBox schema test")

    netbox_url = os.environ["INVENTORY_NETBOX_URL"]
    token = os.environ["INVENTORY_NETBOX_TOKEN"]
    schema = json.loads(SCHEMA_PATH.read_text())
    async with HttpNetBoxBootstrapClient(netbox_url, token) as client:
        await bootstrap_core_fields(client)
        await apply_custom_object_schema(client, schema)
        second = await apply_custom_object_schema(client, schema)

    assert second.changed == 0
    assert second.destructive == 0
