from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from cloud_inventory.api.dependencies import (
    get_mapping_repository,
    get_mapping_target_resolver,
)
from cloud_inventory.api.mappings import MappingConflictError, MappingRecord
from cloud_inventory.app import create_app


class FakeMappingRepository:
    def __init__(self) -> None:
        self.tags: dict[UUID, MappingRecord] = {}
        self.owners: dict[UUID, MappingRecord] = {}

    async def list_tag_mappings(self, **filters: Any) -> list[MappingRecord]:
        return list(self.tags.values())

    async def list_owner_mappings(self, **filters: Any) -> list[MappingRecord]:
        return list(self.owners.values())

    async def upsert_tag_mapping(
        self,
        mapping_id: UUID,
        values: dict[str, Any],
    ) -> tuple[MappingRecord, bool]:
        if values["enabled"] and any(
            item.id != mapping_id
            and item.values["enabled"]
            and item.values["provider"] == values["provider"]
            and item.values["realm"] == values["realm"]
            and item.values["account_id"] == values["account_id"]
            and item.values["source_key"].strip().casefold()
            == values["source_key"].strip().casefold()
            and item.values["source_value"].strip() == values["source_value"].strip()
            for item in self.tags.values()
        ):
            raise MappingConflictError("duplicate active tag mapping")
        created = mapping_id not in self.tags
        record = MappingRecord(id=mapping_id, values=values)
        self.tags[mapping_id] = record
        return record, created

    async def upsert_owner_mapping(
        self,
        mapping_id: UUID,
        values: dict[str, Any],
    ) -> tuple[MappingRecord, bool]:
        created = mapping_id not in self.owners
        record = MappingRecord(id=mapping_id, values=values)
        self.owners[mapping_id] = record
        return record, created


class FakeTargetResolver:
    def __init__(self) -> None:
        self.services = {"payments"}
        self.owners = {70}

    async def business_service_exists(self, service_code: str) -> bool:
        return service_code in self.services

    async def owner_exists(self, owner_id: int) -> bool:
        return owner_id in self.owners


@pytest.fixture
def mapping_api() -> tuple[TestClient, FakeMappingRepository]:
    repository = FakeMappingRepository()
    resolver = FakeTargetResolver()
    app = create_app()
    app.dependency_overrides[get_mapping_repository] = lambda: repository
    app.dependency_overrides[get_mapping_target_resolver] = lambda: resolver
    with TestClient(app) as client:
        yield client, repository


def tag_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "provider": "aws",
        "realm": "commercial",
        "account_id": "111111111111",
        "source_key": "Service",
        "source_value": "payments",
        "business_service_code": "payments",
        "priority": 100,
        "enabled": True,
    }
    body.update(overrides)
    return body


def test_client_uuid_makes_tag_mapping_upsert_idempotent(
    mapping_api: tuple[TestClient, FakeMappingRepository],
) -> None:
    client, _ = mapping_api
    mapping_id = uuid4()

    created = client.put(f"/mappings/tags/{mapping_id}", json=tag_body())
    updated = client.put(
        f"/mappings/tags/{mapping_id}",
        json=tag_body(priority=200),
    )
    listed = client.get("/mappings/tags")

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["priority"] == 200
    assert len(listed.json()["items"]) == 1


def test_enabled_mapping_target_must_exist(
    mapping_api: tuple[TestClient, FakeMappingRepository],
) -> None:
    client, _ = mapping_api
    missing_service = client.put(
        f"/mappings/tags/{uuid4()}",
        json=tag_body(business_service_code="missing"),
    )
    missing_owner = client.put(
        f"/mappings/owners/{uuid4()}",
        json={
            "provider": "aws",
            "realm": "commercial",
            "account_id": "111111111111",
            "source_value": "platform",
            "netbox_owner_id": 404,
            "priority": 100,
            "enabled": True,
        },
    )

    assert missing_service.status_code == 422
    assert missing_owner.status_code == 422


def test_duplicate_active_tag_mapping_returns_conflict(
    mapping_api: tuple[TestClient, FakeMappingRepository],
) -> None:
    client, _ = mapping_api
    assert client.put(
        f"/mappings/tags/{uuid4()}",
        json=tag_body(),
    ).status_code == 201

    duplicate = client.put(f"/mappings/tags/{uuid4()}", json=tag_body())

    assert duplicate.status_code == 409


def test_disabled_mapping_remains_queryable_without_target_validation(
    mapping_api: tuple[TestClient, FakeMappingRepository],
) -> None:
    client, _ = mapping_api
    response = client.put(
        f"/mappings/tags/{uuid4()}",
        json=tag_body(business_service_code="missing", enabled=False),
    )

    assert response.status_code == 201
    listed = client.get("/mappings/tags", params={"enabled": False})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["enabled"] is False
