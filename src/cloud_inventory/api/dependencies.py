from typing import Protocol, cast

from fastapi import Request

from cloud_inventory.config import get_settings
from cloud_inventory.ingest.artifact_store import ArtifactStore, FileSystemArtifactStore
from cloud_inventory.jobs.queue import JobQueue
from cloud_inventory.netbox.client import NetBoxClient
from cloud_inventory.persistence.repositories import ImportRepository


class MappingTargetResolver(Protocol):
    async def business_service_exists(self, service_code: str) -> bool: ...

    async def owner_exists(self, owner_id: int) -> bool: ...


class NetBoxMappingTargetResolver:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url
        self._token = token

    async def business_service_exists(self, service_code: str) -> bool:
        async with NetBoxClient(self._base_url, self._token) as client:
            return await client.get_business_service(service_code) is not None

    async def owner_exists(self, owner_id: int) -> bool:
        async with NetBoxClient(self._base_url, self._token) as client:
            return await client.get_owner(owner_id) is not None


def get_import_repository(request: Request) -> ImportRepository:
    return cast(ImportRepository, request.app.state.import_repository)


def get_mapping_repository(request: Request) -> ImportRepository:
    return cast(ImportRepository, request.app.state.import_repository)


def get_job_queue(request: Request) -> JobQueue:
    return cast(JobQueue, request.app.state.job_queue)


def get_artifact_store(request: Request) -> ArtifactStore:
    store = getattr(request.app.state, "artifact_store", None)
    if store is None:
        store = FileSystemArtifactStore(get_settings().artifact_root)
        request.app.state.artifact_store = store
    return store


def get_mapping_target_resolver(request: Request) -> MappingTargetResolver:
    return cast(MappingTargetResolver, request.app.state.mapping_target_resolver)
