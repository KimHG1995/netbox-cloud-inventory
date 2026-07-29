import hashlib
import json
from copy import deepcopy
from typing import Any, cast

from pydantic import JsonValue

from cloud_inventory.domain.models import CloudResource, normalize_key

USER_MANAGED_ATTRIBUTE_KEYS = frozenset(
    {
        "description",
        "comments",
        "comment",
        "owner",
        "businessservice",
        "businessservices",
        "runbookurl",
        "repositoryurl",
    }
)


def is_empty_observation(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().casefold() == "unknown"
    return isinstance(value, (dict, list)) and not value


def collector_attributes(attributes: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        key: deepcopy(value)
        for key, value in attributes.items()
        if normalize_key(key) not in USER_MANAGED_ATTRIBUTE_KEYS
    }


def managed_resource_document(resource: CloudResource) -> dict[str, Any]:
    return {
        "cloud_uid": resource.uid,
        "provider": resource.provider.value,
        "realm": resource.realm.value,
        "account_id": resource.account_id,
        "region_name": resource.region,
        "resource_type": resource.resource_type.value,
        "external_id": resource.external_id,
        "name": resource.name,
        "cloud_status": resource.status,
        "source_attributes": collector_attributes(resource.attributes),
        "source_tags": dict(resource.tags),
        "relationships": sorted(
            (
                {
                    "relation_type": relation.relation_type,
                    "target_uid": relation.target_uid,
                }
                for relation in resource.relationships
            ),
            key=lambda item: (item["relation_type"], item["target_uid"]),
        ),
        "last_seen_at": resource.observed_at.isoformat(),
        "collection_source": resource.source,
        "sync_state": "current",
    }


def compute_fingerprint(resource: CloudResource) -> str:
    document = managed_resource_document(resource)
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _merge_partial_value(incoming: JsonValue, current: JsonValue) -> JsonValue:
    if is_empty_observation(incoming):
        return deepcopy(current)
    if isinstance(incoming, dict) and isinstance(current, dict):
        merged = deepcopy(current)
        for key, value in incoming.items():
            if key in current:
                merged[key] = _merge_partial_value(value, current[key])
            else:
                merged[key] = deepcopy(value)
        return merged
    return deepcopy(incoming)


def merge_partial_resource(
    incoming: CloudResource,
    current: CloudResource,
) -> CloudResource:
    document = incoming.model_dump()
    for field in ("name", "status"):
        incoming_value = document[field]
        if is_empty_observation(incoming_value):
            document[field] = getattr(current, field)

    merged_attributes = _merge_partial_value(
        cast(JsonValue, collector_attributes(incoming.attributes)),
        cast(JsonValue, collector_attributes(current.attributes)),
    )
    document["attributes"] = collector_attributes(
        cast(dict[str, JsonValue], merged_attributes)
    )
    document["tags"] = _merge_partial_value(
        cast(JsonValue, incoming.tags),
        cast(JsonValue, current.tags),
    )
    if not incoming.relationships:
        document["relationships"] = current.relationships
    return CloudResource.model_validate(document)
