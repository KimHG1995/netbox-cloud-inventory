from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from cloud_inventory.api.dependencies import (
    MappingTargetResolver,
    get_mapping_repository,
    get_mapping_target_resolver,
)
from cloud_inventory.domain.models import Provider, Realm

router = APIRouter(prefix="/mappings")


class MappingConflictError(ValueError):
    """An enabled mapping duplicates another enabled source selector."""


class TagMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Provider
    realm: Realm
    account_id: str = Field(min_length=1, max_length=128)
    source_key: str = Field(min_length=1, max_length=255)
    source_value: str = Field(min_length=1, max_length=1024)
    business_service_code: str = Field(min_length=1, max_length=255)
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True


class OwnerMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Provider
    realm: Realm
    account_id: str = Field(min_length=1, max_length=128)
    source_value: str = Field(min_length=1, max_length=1024)
    netbox_owner_id: int = Field(gt=0)
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True


class MappingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    values: dict[str, Any]


class MappingRepositoryPort(Protocol):
    async def list_tag_mappings(self, **filters: Any) -> list[MappingRecord]: ...

    async def list_owner_mappings(self, **filters: Any) -> list[MappingRecord]: ...

    async def upsert_tag_mapping(
        self,
        mapping_id: UUID,
        values: dict[str, Any],
    ) -> tuple[MappingRecord, bool]: ...

    async def upsert_owner_mapping(
        self,
        mapping_id: UUID,
        values: dict[str, Any],
    ) -> tuple[MappingRecord, bool]: ...


def _mapping_document(record: MappingRecord) -> dict[str, Any]:
    return {"id": str(record.id), **record.values}


@router.get("/tags")
async def list_tag_mappings(
    repository: Annotated[MappingRepositoryPort, Depends(get_mapping_repository)],
    provider: Provider | None = None,
    realm: Realm | None = None,
    account_id: str | None = None,
    enabled: bool | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    records = await repository.list_tag_mappings(
        provider=provider.value if provider else None,
        realm=realm.value if realm else None,
        account_id=account_id,
        enabled=enabled,
        offset=offset,
        limit=limit,
    )
    return {"items": [_mapping_document(record) for record in records]}


@router.put("/tags/{mapping_id}")
async def upsert_tag_mapping(
    mapping_id: UUID,
    request: TagMappingRequest,
    repository: Annotated[MappingRepositoryPort, Depends(get_mapping_repository)],
    resolver: Annotated[
        MappingTargetResolver,
        Depends(get_mapping_target_resolver),
    ],
) -> JSONResponse:
    if request.enabled and not await resolver.business_service_exists(
        request.business_service_code
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="BusinessService does not exist",
        )
    values = request.model_dump(mode="json")
    try:
        record, created = await repository.upsert_tag_mapping(mapping_id, values)
    except MappingConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return JSONResponse(
        _mapping_document(record),
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@router.get("/owners")
async def list_owner_mappings(
    repository: Annotated[MappingRepositoryPort, Depends(get_mapping_repository)],
    provider: Provider | None = None,
    realm: Realm | None = None,
    account_id: str | None = None,
    enabled: bool | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    records = await repository.list_owner_mappings(
        provider=provider.value if provider else None,
        realm=realm.value if realm else None,
        account_id=account_id,
        enabled=enabled,
        offset=offset,
        limit=limit,
    )
    return {"items": [_mapping_document(record) for record in records]}


@router.put("/owners/{mapping_id}")
async def upsert_owner_mapping(
    mapping_id: UUID,
    request: OwnerMappingRequest,
    repository: Annotated[MappingRepositoryPort, Depends(get_mapping_repository)],
    resolver: Annotated[
        MappingTargetResolver,
        Depends(get_mapping_target_resolver),
    ],
) -> JSONResponse:
    if request.enabled and not await resolver.owner_exists(request.netbox_owner_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="NetBox Owner does not exist",
        )
    values = request.model_dump(mode="json")
    try:
        record, created = await repository.upsert_owner_mapping(mapping_id, values)
    except MappingConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return JSONResponse(
        _mapping_document(record),
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )
