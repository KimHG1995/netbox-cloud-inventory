import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Self, cast

import httpx
from pydantic import JsonValue

from cloud_inventory.domain.models import ResourceType, is_sensitive_key

NETBOX_ENDPOINTS = {
    ResourceType.CLOUD_ACCOUNT: "/api/plugins/custom-objects/cloud-account/",
    ResourceType.REGION: "/api/dcim/regions/",
    ResourceType.ZONE: "/api/dcim/sites/",
    ResourceType.VPC: "/api/ipam/vrfs/",
    ResourceType.SUBNET: "/api/ipam/prefixes/",
    ResourceType.VIRTUAL_MACHINE: "/api/virtualization/virtual-machines/",
    ResourceType.NETWORK_INTERFACE: (
        "/api/plugins/custom-objects/cloud-network-interface/"
    ),
    ResourceType.IP_ADDRESS: "/api/ipam/ip-addresses/",
    ResourceType.LOAD_BALANCER: (
        "/api/plugins/custom-objects/cloud-load-balancer/"
    ),
    ResourceType.MANAGED_DATABASE: (
        "/api/plugins/custom-objects/managed-database/"
    ),
    ResourceType.OBJECT_BUCKET: "/api/plugins/custom-objects/object-bucket/",
    ResourceType.DOMAIN: "/api/plugins/custom-objects/domain/",
    ResourceType.DNS_ZONE: "/api/plugins/custom-objects/dns-zone/",
    ResourceType.DNS_RECORD: "/api/plugins/custom-objects/dns-record/",
}
VM_INTERFACE_ENDPOINT = "/api/virtualization/interfaces/"

CUSTOM_OBJECT_TYPES = frozenset(
    {
        ResourceType.CLOUD_ACCOUNT,
        ResourceType.NETWORK_INTERFACE,
        ResourceType.LOAD_BALANCER,
        ResourceType.MANAGED_DATABASE,
        ResourceType.OBJECT_BUCKET,
        ResourceType.DOMAIN,
        ResourceType.DNS_ZONE,
        ResourceType.DNS_RECORD,
    }
)

Sleep = Callable[[float], Awaitable[None]]


class NetBoxRequestError(RuntimeError):
    """A sanitized NetBox request failure."""


class DuplicateCloudUidError(NetBoxRequestError):
    """A cloud UID resolved to more than one NetBox object."""


class PreconditionFailedError(NetBoxRequestError):
    """The object changed after it was read."""


@dataclass(frozen=True)
class NetBoxObject:
    id: int
    resource_type: ResourceType
    data: dict[str, Any]
    etag: str


def _contains_sensitive_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            is_sensitive_key(str(key)) or _contains_sensitive_key(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 30.0))
        except ValueError:
            try:
                target = parsedate_to_datetime(retry_after)
                return max(
                    0.0,
                    min(
                        (target - datetime.now(target.tzinfo)).total_seconds(),
                        30.0,
                    ),
                )
            except (TypeError, ValueError, OverflowError):
                pass
    return float(min(0.25 * 2**attempt, 2.0))


class NetBoxClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        sleep: Sleep = asyncio.sleep,
        max_attempts: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=10.0,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, JsonValue] | None = None,
        headers: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response:
        if payload is not None and _contains_sensitive_key(payload):
            raise NetBoxRequestError("request payload contains a forbidden sensitive key")

        for attempt in range(self._max_attempts):
            try:
                response = await self._client.request(
                    method,
                    endpoint,
                    params=params,
                    json=payload,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                if attempt + 1 < self._max_attempts:
                    await self._sleep(min(0.25 * 2**attempt, 2.0))
                    continue
                raise NetBoxRequestError(
                    f"NetBox {method} {endpoint} failed with a transport error"
                ) from exc

            if response.status_code == 412:
                raise PreconditionFailedError(
                    f"NetBox {method} {endpoint} failed precondition"
                )
            if response.status_code == 404 and allow_not_found:
                return response
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt + 1 < self._max_attempts:
                await self._sleep(_retry_delay(response, attempt))
                continue
            if response.is_error:
                raise NetBoxRequestError(
                    f"NetBox {method} {endpoint} returned {response.status_code}"
                )
            return response

        raise AssertionError("request retry loop ended unexpectedly")

    @staticmethod
    def _document(response: httpx.Response) -> dict[str, Any]:
        document = response.json()
        if not isinstance(document, dict):
            raise NetBoxRequestError("NetBox returned a non-object JSON response")
        return cast(dict[str, Any], document)

    async def get_by_cloud_uid(
        self,
        resource_type: ResourceType,
        cloud_uid: str,
    ) -> NetBoxObject | None:
        endpoint = NETBOX_ENDPOINTS[resource_type]
        filter_name = (
            "cloud_uid"
            if resource_type in CUSTOM_OBJECT_TYPES
            else "cf_cloud_uid"
        )
        response = await self._request(
            "GET",
            endpoint,
            params={filter_name: cloud_uid},
        )
        document = self._document(response)
        raw_results = document.get("results", [])
        if not isinstance(raw_results, list):
            raise NetBoxRequestError("NetBox list response has invalid results")
        if not raw_results:
            return None
        if len(raw_results) > 1:
            raise DuplicateCloudUidError(
                f"multiple {resource_type.value} objects use the same cloud UID"
            )
        summary = raw_results[0]
        if not isinstance(summary, dict) or "id" not in summary:
            raise NetBoxRequestError("NetBox list result has no object ID")

        detail = await self._request("GET", f"{endpoint}{int(summary['id'])}/")
        return NetBoxObject(
            id=int(summary["id"]),
            resource_type=resource_type,
            data=self._document(detail),
            etag=detail.headers.get("ETag", ""),
        )

    async def create(
        self,
        resource_type: ResourceType,
        payload: dict[str, JsonValue],
    ) -> NetBoxObject:
        response = await self._request(
            "POST",
            NETBOX_ENDPOINTS[resource_type],
            payload=payload,
        )
        document = self._document(response)
        return NetBoxObject(
            id=int(document["id"]),
            resource_type=resource_type,
            data=document,
            etag=response.headers.get("ETag", ""),
        )

    async def update(
        self,
        resource_type: ResourceType,
        object_id: int,
        payload: dict[str, JsonValue],
        etag: str,
    ) -> NetBoxObject:
        response = await self._request(
            "PATCH",
            f"{NETBOX_ENDPOINTS[resource_type]}{object_id}/",
            payload=payload,
            headers={"If-Match": etag},
        )
        document = self._document(response)
        return NetBoxObject(
            id=int(document["id"]),
            resource_type=resource_type,
            data=document,
            etag=response.headers.get("ETag", ""),
        )

    async def resolve_relation(self, cloud_uid: str) -> NetBoxObject | None:
        match: NetBoxObject | None = None
        for resource_type in ResourceType:
            candidate = await self.get_by_cloud_uid(resource_type, cloud_uid)
            if candidate is None:
                continue
            if match is not None:
                raise DuplicateCloudUidError(
                    "cloud UID resolved to multiple NetBox object types"
                )
            match = candidate
        return match

    async def get_vm_interface_by_cloud_uid(
        self,
        cloud_uid: str,
    ) -> NetBoxObject | None:
        response = await self._request(
            "GET",
            VM_INTERFACE_ENDPOINT,
            params={"cf_cloud_uid": cloud_uid},
        )
        results = self._document(response).get("results", [])
        if not isinstance(results, list):
            raise NetBoxRequestError("NetBox VMInterface response has invalid results")
        if not results:
            return None
        if len(results) > 1:
            raise DuplicateCloudUidError(
                "multiple VMInterface objects use the same cloud UID"
            )
        summary = results[0]
        if not isinstance(summary, dict) or "id" not in summary:
            raise NetBoxRequestError("NetBox VMInterface result has no object ID")
        detail = await self._request(
            "GET",
            f"{VM_INTERFACE_ENDPOINT}{int(summary['id'])}/",
        )
        return NetBoxObject(
            id=int(summary["id"]),
            resource_type=ResourceType.NETWORK_INTERFACE,
            data=self._document(detail),
            etag=detail.headers.get("ETag", ""),
        )

    async def create_vm_interface(
        self,
        payload: dict[str, JsonValue],
    ) -> NetBoxObject:
        response = await self._request(
            "POST",
            VM_INTERFACE_ENDPOINT,
            payload=payload,
        )
        document = self._document(response)
        return NetBoxObject(
            id=int(document["id"]),
            resource_type=ResourceType.NETWORK_INTERFACE,
            data=document,
            etag=response.headers.get("ETag", ""),
        )

    async def update_vm_interface(
        self,
        object_id: int,
        payload: dict[str, JsonValue],
        etag: str,
    ) -> NetBoxObject:
        response = await self._request(
            "PATCH",
            f"{VM_INTERFACE_ENDPOINT}{object_id}/",
            payload=payload,
            headers={"If-Match": etag},
        )
        document = self._document(response)
        return NetBoxObject(
            id=int(document["id"]),
            resource_type=ResourceType.NETWORK_INTERFACE,
            data=document,
            etag=response.headers.get("ETag", ""),
        )

    async def get_business_service(self, service_code: str) -> dict[str, Any] | None:
        endpoint = "/api/plugins/custom-objects/business-service/"
        response = await self._request(
            "GET",
            endpoint,
            params={"service_code": service_code},
        )
        results = self._document(response).get("results", [])
        if not isinstance(results, list) or len(results) != 1:
            return None
        result = results[0]
        return cast(dict[str, Any], result) if isinstance(result, dict) else None

    async def get_owner(self, owner_id: int) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            f"/api/users/owners/{owner_id}/",
            allow_not_found=True,
        )
        if response.status_code == 404:
            return None
        return self._document(response)

    async def patch_business_service_resources(
        self,
        service_id: int,
        resources: list[dict[str, JsonValue]],
    ) -> None:
        await self._request(
            "PATCH",
            f"/api/plugins/custom-objects/business-service/{service_id}/",
            payload={"resources": cast(JsonValue, resources)},
        )
