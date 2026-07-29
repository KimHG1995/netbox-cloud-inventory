import hashlib
import inspect
import ipaddress
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

from cloud_inventory.domain.models import (
    CloudResource,
    ResourceType,
)
from cloud_inventory.domain.uid import build_cloud_uid
from cloud_inventory.netbox.client import NetBoxObject, PreconditionFailedError
from cloud_inventory.reconciliation.diff import (
    ChangeAction,
    PreviewResult,
    ResourceChange,
)
from cloud_inventory.reconciliation.fingerprint import collector_attributes

CORE_TYPES = frozenset(
    {
        ResourceType.REGION,
        ResourceType.ZONE,
        ResourceType.VPC,
        ResourceType.SUBNET,
        ResourceType.VIRTUAL_MACHINE,
        ResourceType.IP_ADDRESS,
    }
)
ACCOUNT_RELATED_CUSTOM_TYPES = frozenset(
    {
        ResourceType.NETWORK_INTERFACE,
        ResourceType.LOAD_BALANCER,
        ResourceType.MANAGED_DATABASE,
        ResourceType.OBJECT_BUCKET,
        ResourceType.DOMAIN,
        ResourceType.DNS_ZONE,
    }
)
STAGES: dict[int, tuple[ResourceType, ...]] = {
    1: (
        ResourceType.CLOUD_ACCOUNT,
        ResourceType.REGION,
        ResourceType.ZONE,
    ),
    2: (ResourceType.VPC, ResourceType.SUBNET),
    3: (ResourceType.VIRTUAL_MACHINE, ResourceType.NETWORK_INTERFACE),
    4: (ResourceType.IP_ADDRESS,),
    5: (
        ResourceType.LOAD_BALANCER,
        ResourceType.MANAGED_DATABASE,
        ResourceType.OBJECT_BUCKET,
    ),
    6: (
        ResourceType.DOMAIN,
        ResourceType.DNS_ZONE,
        ResourceType.DNS_RECORD,
    ),
    7: (),
    8: (),
    9: (),
}
VM_STATUS = {
    "provisioning": "staged",
    "active": "active",
    "stopped": "offline",
    "degraded": "active",
    "failed": "failed",
    "deleting": "decommissioning",
    "inactive": "offline",
    "unknown": "offline",
}

CheckpointCallback = Callable[[int], Awaitable[None] | None]


class NetBoxWriteClient(Protocol):
    async def get_by_cloud_uid(
        self,
        resource_type: ResourceType,
        cloud_uid: str,
    ) -> NetBoxObject | None: ...

    async def create(
        self,
        resource_type: ResourceType,
        payload: dict[str, JsonValue],
    ) -> NetBoxObject: ...

    async def update(
        self,
        resource_type: ResourceType,
        object_id: int,
        payload: dict[str, JsonValue],
        etag: str,
    ) -> NetBoxObject: ...

    async def get_business_service(self, service_code: str) -> dict[str, Any] | None: ...

    async def get_owner(self, owner_id: int) -> dict[str, Any] | None: ...

    async def patch_business_service_resources(
        self,
        service_id: int,
        resources: list[dict[str, JsonValue]],
    ) -> None: ...

    async def get_vm_interface_by_cloud_uid(
        self,
        cloud_uid: str,
    ) -> NetBoxObject | None: ...

    async def create_vm_interface(
        self,
        payload: dict[str, JsonValue],
    ) -> NetBoxObject: ...

    async def update_vm_interface(
        self,
        object_id: int,
        payload: dict[str, JsonValue],
        etag: str,
    ) -> NetBoxObject: ...


class TagMappingLike(Protocol):
    id: Any
    provider: str
    realm: str
    account_id: str
    source_key: str
    source_value: str
    business_service_code: str
    priority: int
    enabled: bool


class OwnerMappingLike(Protocol):
    id: Any
    provider: str
    realm: str
    account_id: str
    source_value: str
    netbox_owner_id: int
    priority: int
    enabled: bool


class AppliedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cloud_uid: str
    action: str
    changed_fields: list[str]
    warning_codes: list[str]
    netbox_object_id: int | None = None


class ApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: int
    updated: int
    unchanged: int
    warnings: int
    failed: int
    checkpoint: int
    changes: list[AppliedChange]


@dataclass(frozen=True)
class Materialized:
    payload: dict[str, JsonValue] | None
    warning: str | None = None


def deterministic_slug(name: str, cloud_uid: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    prefix = normalized[:80].rstrip("-") or "cloud-resource"
    suffix = hashlib.sha256(cloud_uid.encode()).hexdigest()[:12]
    return f"{prefix}-{suffix}"


def _json_attributes(resource: CloudResource) -> dict[str, JsonValue]:
    return collector_attributes(resource.attributes)


def _common_custom_fields(resource: CloudResource) -> dict[str, JsonValue]:
    return {
        "cloud_uid": resource.uid,
        "provider": resource.provider.value,
        "realm": resource.realm.value,
        "account_id": resource.account_id,
        "external_id": resource.external_id,
        "collection_source": resource.source,
        "cloud_status": resource.status,
        "last_seen_at": resource.observed_at.isoformat(),
        "sync_state": "current",
        "source_tags": dict(resource.tags),
        "source_attributes": _json_attributes(resource),
    }


def _common_custom_object(resource: CloudResource) -> dict[str, JsonValue]:
    return {
        "name": resource.name,
        "cloud_uid": resource.uid,
        "external_id": resource.external_id,
        "provider": resource.provider.value,
        "realm": resource.realm.value,
        "account_id": resource.account_id,
        "region_name": resource.region,
        "status": resource.status,
        "last_seen_at": resource.observed_at.isoformat(),
        "sync_state": "current",
        "source_tags": dict(resource.tags),
        "source_attributes": _json_attributes(resource),
    }


def _copy_attributes(
    payload: dict[str, JsonValue],
    resource: CloudResource,
    names: Sequence[str],
) -> None:
    for name in names:
        value = resource.attributes.get(name)
        if value is not None and value != "" and value != [] and value != {}:
            payload[name] = value


def _normalize_ip_address(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    try:
        if "/" in candidate:
            return str(ipaddress.ip_interface(candidate))
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    prefix = 32 if address.version == 4 else 128
    return f"{address}/{prefix}"


def _normalize_prefix(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return str(ipaddress.ip_network(value.strip(), strict=False))
    except ValueError:
        return None


def _payload_matches(current: dict[str, Any], desired: dict[str, JsonValue]) -> bool:
    for key, value in desired.items():
        current_value = current.get(key)
        if key == "custom_fields" and isinstance(current_value, dict):
            if not isinstance(value, dict) or not _payload_matches(current_value, value):
                return False
        elif isinstance(current_value, dict) and "value" in current_value:
            if current_value["value"] != value:
                return False
        elif current_value != value:
            return False
    return True


class NetBoxWriter:
    def __init__(
        self,
        client: NetBoxWriteClient,
        *,
        checkpoint: CheckpointCallback | None = None,
        tag_mappings: Sequence[TagMappingLike] = (),
        owner_mappings: Sequence[OwnerMappingLike] = (),
    ) -> None:
        self._client = client
        self._checkpoint_callback = checkpoint
        self._tag_mappings = tag_mappings
        self._owner_mappings = owner_mappings
        self._resolved: dict[str, NetBoxObject] = {}

    @staticmethod
    def _in_scope(mapping: TagMappingLike | OwnerMappingLike, resource: CloudResource) -> bool:
        return (
            mapping.enabled
            and mapping.provider == resource.provider.value
            and mapping.realm == resource.realm.value
            and mapping.account_id == resource.account_id
        )

    def _service_mapping(
        self,
        resource: CloudResource,
    ) -> tuple[TagMappingLike | None, str | None]:
        service_keys = {"service", "application", "app"}
        hints = [
            (key.strip().casefold(), value.strip())
            for key, value in resource.tags.items()
            if key.strip().casefold() in service_keys and value.strip()
        ]
        if not hints:
            return None, None
        candidates = [
            mapping
            for mapping in self._tag_mappings
            if self._in_scope(mapping, resource)
            and (mapping.source_key.strip().casefold(), mapping.source_value.strip())
            in hints
        ]
        if not candidates:
            return None, "unresolved_service"
        candidates.sort(key=lambda item: (-item.priority, str(item.id)))
        highest = [
            item for item in candidates if item.priority == candidates[0].priority
        ]
        if len({item.business_service_code for item in highest}) > 1:
            return None, "ambiguous_mapping"
        return highest[0], None

    def _owner_mapping(
        self,
        resource: CloudResource,
    ) -> tuple[OwnerMappingLike | None, str | None]:
        owner_keys = {"owner", "team", "managed-by"}
        hints = {
            value.strip()
            for key, value in resource.tags.items()
            if key.strip().casefold() in owner_keys and value.strip()
        }
        if not hints:
            return None, None
        candidates = [
            mapping
            for mapping in self._owner_mappings
            if self._in_scope(mapping, resource)
            and mapping.source_value.strip() in hints
        ]
        if not candidates:
            return None, "unresolved_owner"
        candidates.sort(key=lambda item: (-item.priority, str(item.id)))
        highest = [
            item for item in candidates if item.priority == candidates[0].priority
        ]
        if len({item.netbox_owner_id for item in highest}) > 1:
            return None, "ambiguous_mapping"
        return highest[0], None

    @staticmethod
    def _object_type(resource_type: ResourceType) -> str:
        values = {
            ResourceType.CLOUD_ACCOUNT: "custom-objects/cloud-account",
            ResourceType.REGION: "dcim/region",
            ResourceType.ZONE: "dcim/site",
            ResourceType.VPC: "ipam/vrf",
            ResourceType.SUBNET: "ipam/prefix",
            ResourceType.VIRTUAL_MACHINE: "virtualization/virtualmachine",
            ResourceType.NETWORK_INTERFACE: (
                "custom-objects/cloud-network-interface"
            ),
            ResourceType.IP_ADDRESS: "ipam/ipaddress",
            ResourceType.LOAD_BALANCER: "custom-objects/cloud-load-balancer",
            ResourceType.MANAGED_DATABASE: "custom-objects/managed-database",
            ResourceType.OBJECT_BUCKET: "custom-objects/object-bucket",
            ResourceType.DOMAIN: "custom-objects/domain",
            ResourceType.DNS_ZONE: "custom-objects/dns-zone",
            ResourceType.DNS_RECORD: "custom-objects/dns-record",
        }
        return values[resource_type]

    @staticmethod
    def _owner_id(value: object) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, dict) and value.get("id") is not None:
            return int(value["id"])
        return None

    async def _apply_mappings(
        self,
        preview: PreviewResult,
        applied: dict[str, AppliedChange],
    ) -> None:
        for change in preview.changes:
            result = applied.get(change.cloud_uid)
            netbox_object = self._resolved.get(change.cloud_uid)
            if result is None or netbox_object is None or result.action == "error":
                continue

            service_mapping, service_warning = self._service_mapping(change.desired)
            if service_warning is not None:
                result.warning_codes.append(service_warning)
            elif service_mapping is not None:
                service = await self._client.get_business_service(
                    service_mapping.business_service_code
                )
                if service is None:
                    result.warning_codes.append("unresolved_service")
                else:
                    resources = service.get("resources", [])
                    existing_resources = (
                        [dict(item) for item in resources if isinstance(item, dict)]
                        if isinstance(resources, list)
                        else []
                    )
                    reference: dict[str, JsonValue] = {
                        "object_type": self._object_type(change.resource_type),
                        "id": netbox_object.id,
                    }
                    if reference not in existing_resources:
                        await self._client.patch_business_service_resources(
                            int(service["id"]),
                            [*existing_resources, reference],
                        )

            owner_mapping, owner_warning = self._owner_mapping(change.desired)
            if owner_warning is not None:
                result.warning_codes.append(owner_warning)
            elif owner_mapping is not None:
                owner = await self._client.get_owner(owner_mapping.netbox_owner_id)
                if owner is None:
                    result.warning_codes.append("unresolved_owner")
                    continue
                current_owner_id = self._owner_id(netbox_object.data.get("owner"))
                if current_owner_id is not None and current_owner_id != int(owner["id"]):
                    result.warning_codes.append("owner_conflict")
                    continue
                if current_owner_id is None:
                    try:
                        updated = await self._client.update(
                            change.resource_type,
                            netbox_object.id,
                            {"owner": int(owner["id"])},
                            netbox_object.etag,
                        )
                    except PreconditionFailedError:
                        result.warning_codes.append("concurrent_update")
                    else:
                        self._resolved[change.cloud_uid] = updated
                        result.changed_fields = sorted(
                            {*result.changed_fields, "owner"}
                        )

    async def _save_checkpoint(self, stage: int) -> None:
        if self._checkpoint_callback is None:
            return
        result = self._checkpoint_callback(stage)
        if inspect.isawaitable(result):
            await result

    async def _account_object(
        self,
        resource: CloudResource,
    ) -> NetBoxObject | None:
        account_uid = build_cloud_uid(
            resource.provider,
            resource.realm,
            resource.account_id,
            "global",
            ResourceType.CLOUD_ACCOUNT,
            resource.account_id,
        )
        cached = self._resolved.get(account_uid)
        if cached is not None:
            return cached
        account = await self._client.get_by_cloud_uid(
            ResourceType.CLOUD_ACCOUNT,
            account_uid,
        )
        if account is not None:
            self._resolved[account_uid] = account
        return account

    async def _relation(
        self,
        resource: CloudResource,
        *target_types: ResourceType,
    ) -> NetBoxObject | None:
        for relationship in resource.relationships:
            target = self._resolved.get(relationship.target_uid)
            if target is None:
                continue
            if target.resource_type in target_types:
                return target
        return None

    async def _relations(
        self,
        resource: CloudResource,
        *target_types: ResourceType,
    ) -> list[NetBoxObject]:
        results: list[NetBoxObject] = []
        for relationship in resource.relationships:
            target = self._resolved.get(relationship.target_uid)
            if target is not None and target.resource_type in target_types:
                results.append(target)
        return sorted(results, key=lambda item: (item.resource_type.value, item.id))

    def _polymorphic_reference(
        self,
        netbox_object: NetBoxObject,
    ) -> dict[str, JsonValue]:
        return {
            "object_type": self._object_type(netbox_object.resource_type),
            "id": netbox_object.id,
        }

    async def _materialize(self, resource: CloudResource) -> Materialized:
        if resource.resource_type in CORE_TYPES:
            payload: dict[str, JsonValue] = {
                "custom_fields": _common_custom_fields(resource)
            }
            if resource.resource_type is ResourceType.REGION:
                payload.update(
                    {
                        "name": resource.name,
                        "slug": deterministic_slug(resource.name, resource.uid),
                    }
                )
            elif resource.resource_type is ResourceType.ZONE:
                payload.update(
                    {
                        "name": resource.name,
                        "slug": deterministic_slug(resource.name, resource.uid),
                        "status": "active",
                    }
                )
                region = await self._relation(resource, ResourceType.REGION)
                if region is not None:
                    payload["region"] = region.id
            elif resource.resource_type is ResourceType.VPC:
                payload["name"] = resource.name
            elif resource.resource_type is ResourceType.SUBNET:
                prefix = _normalize_prefix(resource.attributes.get("cidr"))
                if prefix is None:
                    return Materialized(None, "unmaterializable_summary")
                payload.update({"prefix": prefix, "status": "active"})
                vpc = await self._relation(resource, ResourceType.VPC)
                if vpc is not None:
                    payload["vrf"] = vpc.id
            elif resource.resource_type is ResourceType.VIRTUAL_MACHINE:
                payload.update(
                    {
                        "name": resource.name,
                        "status": VM_STATUS.get(resource.status.casefold(), "offline"),
                    }
                )
                zone = await self._relation(resource, ResourceType.ZONE)
                if zone is not None:
                    payload["site"] = zone.id
            elif resource.resource_type is ResourceType.IP_ADDRESS:
                address = _normalize_ip_address(
                    resource.attributes.get("address", resource.external_id)
                )
                if address is None:
                    return Materialized(None, "unmaterializable_summary")
                payload.update({"address": address, "status": "active"})
            return Materialized(payload)

        payload = _common_custom_object(resource)
        if resource.resource_type in ACCOUNT_RELATED_CUSTOM_TYPES:
            account = await self._account_object(resource)
            if account is None:
                return Materialized(None, "blocked_by_dependency")
            payload["cloud_account"] = account.id

        if resource.resource_type is ResourceType.CLOUD_ACCOUNT:
            _copy_attributes(
                payload,
                resource,
                (
                    "collection_mode",
                    "console_url",
                    "last_success_at",
                    "last_run_status",
                ),
            )
        elif resource.resource_type is ResourceType.NETWORK_INTERFACE:
            _copy_attributes(
                payload,
                resource,
                (
                    "mac_address",
                    "private_ips",
                    "public_ips",
                    "attachment_status",
                ),
            )
            region = await self._relation(resource, ResourceType.REGION)
            zone = await self._relation(resource, ResourceType.ZONE)
            vpc = await self._relation(resource, ResourceType.VPC)
            subnet = await self._relation(resource, ResourceType.SUBNET)
            virtual_machine = await self._relation(
                resource,
                ResourceType.VIRTUAL_MACHINE,
            )
            for field_name, relation in (
                ("region", region),
                ("zone", zone),
                ("vpc", vpc),
                ("subnet", subnet),
                ("attached_virtual_machine", virtual_machine),
            ):
                if relation is not None:
                    payload[field_name] = relation.id
        elif resource.resource_type is ResourceType.LOAD_BALANCER:
            _copy_attributes(
                payload,
                resource,
                (
                    "load_balancer_type",
                    "scheme",
                    "dns_name",
                    "listeners",
                    "target_groups",
                ),
            )
            vpc = await self._relation(resource, ResourceType.VPC)
            if vpc is not None:
                payload["vpc"] = vpc.id
            subnets = await self._relations(resource, ResourceType.SUBNET)
            frontend_ips = await self._relations(resource, ResourceType.IP_ADDRESS)
            backends = await self._relations(
                resource,
                ResourceType.VIRTUAL_MACHINE,
                ResourceType.IP_ADDRESS,
                ResourceType.MANAGED_DATABASE,
                ResourceType.OBJECT_BUCKET,
            )
            if subnets:
                payload["subnets"] = [item.id for item in subnets]
            if frontend_ips:
                payload["frontend_ips"] = [item.id for item in frontend_ips]
            if backends:
                payload["backend_resources"] = [
                    self._polymorphic_reference(item) for item in backends
                ]
        elif resource.resource_type is ResourceType.MANAGED_DATABASE:
            _copy_attributes(
                payload,
                resource,
                (
                    "engine",
                    "engine_version",
                    "topology",
                    "endpoint",
                    "port",
                    "public_access",
                    "high_availability",
                    "multi_zone",
                    "encrypted",
                    "backup_enabled",
                ),
            )
            vpc = await self._relation(resource, ResourceType.VPC)
            subnets = await self._relations(resource, ResourceType.SUBNET)
            if vpc is not None:
                payload["vpc"] = vpc.id
            if subnets:
                payload["subnets"] = [item.id for item in subnets]
        elif resource.resource_type is ResourceType.OBJECT_BUCKET:
            _copy_attributes(
                payload,
                resource,
                (
                    "versioning",
                    "encryption",
                    "public_access",
                    "object_lock",
                    "console_url",
                ),
            )
            region = await self._relation(resource, ResourceType.REGION)
            if region is not None:
                payload["region"] = region.id
        elif resource.resource_type is ResourceType.DOMAIN:
            _copy_attributes(
                payload,
                resource,
                (
                    "registrar",
                    "registered_at",
                    "expires_at",
                    "auto_renew",
                    "name_servers",
                    "owner_hint",
                ),
            )
        elif resource.resource_type is ResourceType.DNS_ZONE:
            _copy_attributes(
                payload,
                resource,
                ("visibility", "name_servers", "record_count"),
            )
            vpc_links = await self._relations(resource, ResourceType.VPC)
            if vpc_links:
                payload["vpc_links"] = [item.id for item in vpc_links]
        elif resource.resource_type is ResourceType.DNS_RECORD:
            zone = await self._relation(resource, ResourceType.DNS_ZONE)
            if zone is None:
                return Materialized(None, "blocked_by_dependency")
            payload["zone"] = zone.id
            record_type = resource.attributes.get("record_type")
            values = resource.attributes.get("values")
            if not isinstance(record_type, str) or not record_type or not isinstance(
                values, list
            ):
                return Materialized(None, "unmaterializable_summary")
            payload.update({"record_type": record_type, "values": values})
            _copy_attributes(payload, resource, ("ttl", "alias_target"))
            related = await self._relations(
                resource,
                ResourceType.VIRTUAL_MACHINE,
                ResourceType.IP_ADDRESS,
                ResourceType.LOAD_BALANCER,
                ResourceType.MANAGED_DATABASE,
                ResourceType.OBJECT_BUCKET,
            )
            if related:
                payload["related_resources"] = [
                    self._polymorphic_reference(item) for item in related
                ]
        return Materialized(payload)

    async def _sync_vm_interface(
        self,
        resource: CloudResource,
        cloud_interface: NetBoxObject,
    ) -> tuple[NetBoxObject, str | None]:
        virtual_machine = await self._relation(
            resource,
            ResourceType.VIRTUAL_MACHINE,
        )
        if virtual_machine is None:
            return cloud_interface, None
        payload: dict[str, JsonValue] = {
            "name": resource.name,
            "virtual_machine": virtual_machine.id,
            "enabled": resource.status.casefold() not in {"inactive", "deleting"},
            "custom_fields": _common_custom_fields(resource),
        }
        current = await self._client.get_vm_interface_by_cloud_uid(resource.uid)
        try:
            if current is None:
                vm_interface = await self._client.create_vm_interface(payload)
            elif _payload_matches(current.data, payload):
                vm_interface = current
            else:
                vm_interface = await self._client.update_vm_interface(
                    current.id,
                    payload,
                    current.etag,
                )
            updated_cloud_interface = await self._client.update(
                ResourceType.NETWORK_INTERFACE,
                cloud_interface.id,
                {
                    "attached_virtual_machine": virtual_machine.id,
                    "vm_interface": vm_interface.id,
                },
                cloud_interface.etag,
            )
        except PreconditionFailedError:
            return cloud_interface, "concurrent_update"
        return updated_cloud_interface, None

    async def _write(
        self,
        change: ResourceChange,
        payload: dict[str, JsonValue],
    ) -> tuple[str, NetBoxObject | None, list[str]]:
        current = await self._client.get_by_cloud_uid(
            change.resource_type,
            change.cloud_uid,
        )
        if current is None:
            created = await self._client.create(change.resource_type, payload)
            return "create", created, []
        if _payload_matches(current.data, payload):
            return "unchanged", current, []

        try:
            updated = await self._client.update(
                change.resource_type,
                current.id,
                payload,
                current.etag,
            )
            return "update", updated, []
        except PreconditionFailedError:
            refreshed = await self._client.get_by_cloud_uid(
                change.resource_type,
                change.cloud_uid,
            )
            if refreshed is None:
                return "error", None, ["concurrent_update"]
            if _payload_matches(refreshed.data, payload):
                return "unchanged", refreshed, []
            try:
                updated = await self._client.update(
                    change.resource_type,
                    refreshed.id,
                    payload,
                    refreshed.etag,
                )
                return "update", updated, []
            except PreconditionFailedError:
                return "error", None, ["concurrent_update"]

    async def apply(
        self,
        preview: PreviewResult,
        checkpoint: int = 0,
    ) -> ApplyResult:
        results: list[AppliedChange] = []
        changes_by_type: dict[ResourceType, list[ResourceChange]] = {}
        for change in preview.changes:
            changes_by_type.setdefault(change.resource_type, []).append(change)

        for stage, resource_types in STAGES.items():
            if stage <= checkpoint:
                continue
            for resource_type in resource_types:
                for change in changes_by_type.get(resource_type, []):
                    if change.action is ChangeAction.UNCHANGED:
                        results.append(
                            AppliedChange(
                                cloud_uid=change.cloud_uid,
                                action="unchanged",
                                changed_fields=[],
                                warning_codes=change.warnings,
                            )
                        )
                        continue
                    if change.action in {ChangeAction.WARNING, ChangeAction.ERROR}:
                        results.append(
                            AppliedChange(
                                cloud_uid=change.cloud_uid,
                                action=change.action.value,
                                changed_fields=change.changed_fields,
                                warning_codes=change.warnings,
                            )
                        )
                        continue

                    materialized = await self._materialize(change.desired)
                    if materialized.payload is None:
                        results.append(
                            AppliedChange(
                                cloud_uid=change.cloud_uid,
                                action="warning",
                                changed_fields=change.changed_fields,
                                warning_codes=[
                                    materialized.warning or "unmaterializable_summary"
                                ],
                            )
                        )
                        continue

                    try:
                        action, netbox_object, warnings = await self._write(
                            change,
                            materialized.payload,
                        )
                    except Exception:
                        action, netbox_object, warnings = "error", None, [
                            "netbox_write_failed"
                        ]
                    if netbox_object is not None:
                        if (
                            change.resource_type is ResourceType.NETWORK_INTERFACE
                            and action in {"create", "update", "unchanged"}
                        ):
                            netbox_object, interface_warning = (
                                await self._sync_vm_interface(
                                    change.desired,
                                    netbox_object,
                                )
                            )
                            if interface_warning is not None:
                                warnings.append(interface_warning)
                        self._resolved[change.cloud_uid] = netbox_object
                    results.append(
                        AppliedChange(
                            cloud_uid=change.cloud_uid,
                            action=action,
                            changed_fields=change.changed_fields,
                            warning_codes=[*change.warnings, *warnings],
                            netbox_object_id=(
                                netbox_object.id if netbox_object is not None else None
                            ),
                        )
                    )
            if stage == 7:
                await self._apply_mappings(
                    preview,
                    {item.cloud_uid: item for item in results},
                )
            await self._save_checkpoint(stage)

        return ApplyResult(
            created=sum(item.action == "create" for item in results),
            updated=sum(item.action == "update" for item in results),
            unchanged=sum(item.action == "unchanged" for item in results),
            warnings=sum(
                item.action == "warning" or bool(item.warning_codes)
                for item in results
            ),
            failed=sum(item.action == "error" for item in results),
            checkpoint=max(STAGES),
            changes=results,
        )
