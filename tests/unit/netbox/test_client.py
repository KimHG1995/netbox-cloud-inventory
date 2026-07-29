from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
import respx

from cloud_inventory.domain.models import ResourceType
from cloud_inventory.netbox.client import (
    NetBoxClient,
    NetBoxRequestError,
    PreconditionFailedError,
)


@pytest.mark.asyncio
@respx.mock
async def test_get_by_cloud_uid_uses_encoded_filter_and_fetches_etag() -> None:
    list_route = respx.get(
        "https://netbox.example.test/api/plugins/custom-objects/cloud-account/",
        params={"cloud_uid": "aws:commercial:account/with space"},
    ).mock(return_value=httpx.Response(200, json={"results": [{"id": 42}]}))
    detail_route = respx.get(
        "https://netbox.example.test/api/plugins/custom-objects/cloud-account/42/"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"id": 42, "cloud_uid": "aws:commercial:account/with space"},
            headers={"ETag": '"revision-7"'},
        )
    )

    async with NetBoxClient("https://netbox.example.test", "nbt_key.token") as client:
        result = await client.get_by_cloud_uid(
            ResourceType.CLOUD_ACCOUNT,
            "aws:commercial:account/with space",
        )

    assert list_route.called
    assert detail_route.called
    assert result is not None
    assert result.id == 42
    assert result.etag == '"revision-7"'
    assert list_route.calls[0].request.headers["Authorization"] == "Bearer nbt_key.token"


@pytest.mark.asyncio
@respx.mock
async def test_core_lookup_uses_custom_field_filter() -> None:
    route = respx.get(
        "https://netbox.example.test/api/dcim/regions/",
        params={"cf_cloud_uid": "aws:commercial:region:ap-northeast-2"},
    ).mock(return_value=httpx.Response(200, json={"results": []}))

    async with NetBoxClient("https://netbox.example.test", "token") as client:
        result = await client.get_by_cloud_uid(
            ResourceType.REGION,
            "aws:commercial:region:ap-northeast-2",
        )

    assert route.called
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_retries_429_using_retry_after() -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    route = respx.get(
        "https://netbox.example.test/api/ipam/vrfs/",
        params={"cf_cloud_uid": "vpc-1"},
    ).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0.25"}),
            httpx.Response(200, json={"results": []}),
        ]
    )

    async with NetBoxClient(
        "https://netbox.example.test",
        "token",
        sleep=fake_sleep,
    ) as client:
        result = await client.get_by_cloud_uid(ResourceType.VPC, "vpc-1")

    assert result is None
    assert route.call_count == 2
    assert delays == [0.25]


@pytest.mark.asyncio
@respx.mock
async def test_update_sends_if_match_and_surfaces_precondition_failure() -> None:
    route = respx.patch(
        "https://netbox.example.test/api/ipam/vrfs/9/"
    ).mock(return_value=httpx.Response(412, json={"detail": "changed"}))

    async with NetBoxClient("https://netbox.example.test", "token") as client:
        with pytest.raises(PreconditionFailedError):
            await client.update(
                ResourceType.VPC,
                9,
                {"name": "production"},
                '"etag-1"',
            )

    assert route.calls[0].request.headers["If-Match"] == '"etag-1"'


@pytest.mark.asyncio
@respx.mock
async def test_errors_redact_authorization_and_payload_secrets() -> None:
    respx.post(
        "https://netbox.example.test/api/ipam/vrfs/"
    ).mock(return_value=httpx.Response(500, json={"detail": "failed"}))

    async def no_wait(_delay: float) -> None:
        return None

    async with NetBoxClient(
        "https://netbox.example.test",
        "super-secret-token",
        sleep=no_wait,
    ) as client:
        with pytest.raises(NetBoxRequestError) as error:
            await client.create(
                ResourceType.VPC,
                {"name": "production", "password": "do-not-send"},
            )

    assert "super-secret-token" not in str(error.value)
    assert "do-not-send" not in str(error.value)


Sleep = Callable[[float], Awaitable[Any]]
