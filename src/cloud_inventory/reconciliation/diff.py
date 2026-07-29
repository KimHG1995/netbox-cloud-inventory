from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from cloud_inventory.domain.models import CloudResource, ResourceBatch, ResourceType
from cloud_inventory.reconciliation.fingerprint import (
    compute_fingerprint,
    managed_resource_document,
    merge_partial_resource,
)


def _has_relation_type(resource: CloudResource, resource_type: ResourceType) -> bool:
    marker = f":{resource_type.value}:"
    return any(
        marker in relationship.target_uid
        for relationship in resource.relationships
    )


def _materialization_warning(resource: CloudResource) -> str | None:
    if (
        resource.resource_type is ResourceType.SUBNET
        and not resource.attributes.get("cidr")
    ):
        return "unmaterializable_summary"
    if resource.resource_type is ResourceType.IP_ADDRESS and not (
        resource.attributes.get("address") or resource.external_id
    ):
        return "unmaterializable_summary"
    if (
        resource.resource_type is ResourceType.VIRTUAL_MACHINE
        and not _has_relation_type(resource, ResourceType.ZONE)
    ):
        return "unmaterializable_summary"
    if (
        resource.resource_type is ResourceType.DNS_RECORD
        and not _has_relation_type(resource, ResourceType.DNS_ZONE)
    ):
        return "blocked_by_dependency"
    return None


class ChangeAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    UNCHANGED = "unchanged"
    WARNING = "warning"
    ERROR = "error"


class ResourceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cloud_uid: str
    resource_type: ResourceType
    action: ChangeAction
    changed_fields: list[str]
    warnings: list[str]
    desired: CloudResource


class PreviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_hash: str
    created: int
    updated: int
    unchanged: int
    warnings: int
    errors: int
    changes: list[ResourceChange]


def _changed_fields(current: CloudResource, desired: CloudResource) -> list[str]:
    current_document = managed_resource_document(current)
    desired_document = managed_resource_document(desired)
    field_names = {
        "cloud_uid": "uid",
        "region_name": "region",
        "cloud_status": "status",
        "source_attributes": "attributes",
        "source_tags": "tags",
        "last_seen_at": "observed_at",
    }
    return sorted(
        field_names.get(key, key)
        for key, value in desired_document.items()
        if value != current_document.get(key)
    )


class Reconciler:
    def preview(
        self,
        batch: ResourceBatch,
        current: Mapping[str, CloudResource],
    ) -> PreviewResult:
        changes: list[ResourceChange] = []
        for incoming in sorted(batch.resources, key=lambda item: item.uid):
            current_resource = current.get(incoming.uid)
            if current_resource is None:
                desired = incoming.model_copy(deep=True)
                action = ChangeAction.CREATE
                changed_fields = sorted(managed_resource_document(desired))
            else:
                desired = merge_partial_resource(incoming, current_resource)
                changed_fields = _changed_fields(current_resource, desired)
                action = (
                    ChangeAction.UNCHANGED
                    if compute_fingerprint(current_resource)
                    == compute_fingerprint(desired)
                    else ChangeAction.UPDATE
                )

            warnings = sorted(set(desired.warnings))
            materialization_warning = _materialization_warning(desired)
            if materialization_warning is not None:
                warnings.append(materialization_warning)
                warnings = sorted(set(warnings))
                action = ChangeAction.WARNING
            changes.append(
                ResourceChange(
                    cloud_uid=desired.uid,
                    resource_type=desired.resource_type,
                    action=action,
                    changed_fields=changed_fields,
                    warnings=warnings,
                    desired=desired,
                )
            )

        return PreviewResult(
            batch_hash=batch.content_hash,
            created=sum(change.action is ChangeAction.CREATE for change in changes),
            updated=sum(change.action is ChangeAction.UPDATE for change in changes),
            unchanged=sum(
                change.action is ChangeAction.UNCHANGED for change in changes
            ),
            warnings=sum(bool(change.warnings) for change in changes),
            errors=sum(change.action is ChangeAction.ERROR for change in changes),
            changes=changes,
        )
