import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import JsonValue

from cloud_inventory.domain.models import (
    CloudResource,
    Completeness,
    DetailLevel,
    Provider,
    Realm,
    Relationship,
    ResourceBatch,
    ResourceScope,
    ResourceType,
)
from cloud_inventory.domain.uid import build_cloud_uid

_BATCH_URL_PREFIX = (
    "https://github.com/KimHG1995/netbox-cloud-inventory/batches/"
)


def _utc_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _resource_document(resource: CloudResource) -> dict[str, Any]:
    document = resource.model_dump(mode="json")
    document["observed_at"] = _utc_z(resource.observed_at)
    return document


def canonical_resource_sha256(resource: CloudResource) -> str:
    return hashlib.sha256(_canonical_json(_resource_document(resource)).encode()).hexdigest()


def _batch_content_document(
    *,
    provider: Provider,
    realm: Realm,
    account_id: str,
    observed_at: datetime,
    scopes: Sequence[ResourceScope],
    resources: Sequence[CloudResource],
    parser_profiles: Sequence[str],
    warnings: Sequence[str],
) -> dict[str, object]:
    return {
        "schema_version": "1",
        "provider": provider.value,
        "realm": realm.value,
        "account_id": account_id,
        "observed_at": _utc_z(observed_at),
        "completeness": Completeness.PARTIAL.value,
        "scopes": [
            scope.model_dump(mode="json")
            for scope in sorted(
                scopes,
                key=lambda item: (item.region, item.resource_type.value),
            )
        ],
        "resources": [
            _resource_document(resource)
            for resource in sorted(resources, key=lambda item: item.uid)
        ],
        "parser_profiles": sorted(set(parser_profiles)),
        "warnings": sorted(set(warnings)),
    }


def _batch_id(content_hash: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{_BATCH_URL_PREFIX}{content_hash}")


def _sort_relationships(
    relationships: Sequence[Relationship],
) -> list[Relationship]:
    unique = {
        (relationship.relation_type, relationship.target_uid)
        for relationship in relationships
    }
    return [
        Relationship(relation_type=relation_type, target_uid=target_uid)
        for relation_type, target_uid in sorted(unique)
    ]


def finalize_batch(
    *,
    provider: Provider,
    realm: Realm,
    account_id: str,
    observed_at: datetime,
    resources: Sequence[CloudResource],
    parser_profile: str,
    source_priority: int,
    detail_level: DetailLevel,
    warnings: Sequence[str] = (),
) -> ResourceBatch:
    normalized_resources = [resource.model_copy(deep=True) for resource in resources]
    account_resources = [
        resource
        for resource in normalized_resources
        if resource.resource_type is ResourceType.CLOUD_ACCOUNT
    ]
    if len(account_resources) > 1:
        raise ValueError("a Batch must contain exactly one cloud_account resource")

    if account_resources:
        account = account_resources[0]
    else:
        account = CloudResource(
            uid=build_cloud_uid(
                provider,
                realm,
                account_id,
                "global",
                ResourceType.CLOUD_ACCOUNT,
                account_id,
            ),
            provider=provider,
            realm=realm,
            account_id=account_id,
            region="global",
            resource_type=ResourceType.CLOUD_ACCOUNT,
            external_id=account_id,
            name=f"{provider.value}:{realm.value}:{account_id}",
            status="unknown",
            observed_at=observed_at,
            completeness=Completeness.PARTIAL,
            detail_level=detail_level,
            source_profile=parser_profile,
            source_priority=source_priority,
        )
        normalized_resources.append(account)

    region_uids = sorted(
        resource.uid
        for resource in normalized_resources
        if resource.resource_type is ResourceType.REGION
    )
    account.relationships = _sort_relationships(
        [
            *account.relationships,
            *[
                Relationship(relation_type="contains", target_uid=region_uid)
                for region_uid in region_uids
            ],
        ]
    )

    for resource in normalized_resources:
        identity = (resource.provider, resource.realm, resource.account_id)
        if identity != (provider, realm, account_id):
            raise ValueError("resource provider, realm, and account must match the Batch")
        resource.relationships = _sort_relationships(resource.relationships)

    normalized_resources.sort(key=lambda resource: resource.uid)
    scopes = [
        ResourceScope(
            region=region,
            resource_type=resource_type,
            completeness=Completeness.PARTIAL,
        )
        for region, resource_type in sorted(
            {
                (resource.region, resource.resource_type)
                for resource in normalized_resources
            },
            key=lambda item: (item[0], item[1].value),
        )
    ]
    normalized_warnings = sorted(
        {
            *warnings,
            *(
                warning
                for resource in normalized_resources
                for warning in resource.warnings
            ),
        }
    )
    content_document = _batch_content_document(
        provider=provider,
        realm=realm,
        account_id=account_id,
        observed_at=observed_at,
        scopes=scopes,
        resources=normalized_resources,
        parser_profiles=[parser_profile],
        warnings=normalized_warnings,
    )
    content_hash = hashlib.sha256(_canonical_json(content_document).encode()).hexdigest()

    return ResourceBatch(
        batch_id=_batch_id(content_hash),
        provider=provider,
        realm=realm,
        account_id=account_id,
        observed_at=observed_at,
        completeness=Completeness.PARTIAL,
        scopes=scopes,
        resources=normalized_resources,
        parser_profiles=[parser_profile],
        warnings=normalized_warnings,
        content_hash=content_hash,
    )


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value == "" or value.casefold() == "unknown"
    return isinstance(value, (list, dict)) and not value


def _candidate_rank(resource: CloudResource) -> tuple[datetime, int, str, str]:
    return (
        resource.observed_at,
        resource.source_priority,
        resource.source_profile,
        canonical_resource_sha256(resource),
    )


def _ranked_value(
    candidates: Sequence[CloudResource],
    getter: Any,
) -> Any:
    values = [getter(candidate) for candidate in candidates]
    return next((value for value in values if not _is_empty(value)), values[0])


def _merge_resources(candidates: Sequence[CloudResource]) -> CloudResource:
    ranked = sorted(candidates, key=_candidate_rank, reverse=True)
    winner = ranked[0]
    identity = {
        (
            resource.provider,
            resource.realm,
            resource.account_id,
            resource.region,
            resource.resource_type,
            resource.external_id,
        )
        for resource in ranked
    }
    if len(identity) != 1:
        raise ValueError(f"conflicting canonical identity for uid {winner.uid}")

    attributes: dict[str, JsonValue] = {}
    for key in sorted({key for resource in ranked for key in resource.attributes}):
        attributes[key] = _ranked_value(
            [resource for resource in ranked if key in resource.attributes],
            lambda resource, attribute_key=key: resource.attributes[attribute_key],
        )

    tags: dict[str, str] = {}
    for key in sorted({key for resource in ranked for key in resource.tags}):
        tags[key] = _ranked_value(
            [resource for resource in ranked if key in resource.tags],
            lambda resource, tag_key=key: resource.tags[tag_key],
        )

    document = winner.model_dump()
    document.update(
        {
            "name": _ranked_value(ranked, lambda resource: resource.name),
            "status": _ranked_value(ranked, lambda resource: resource.status),
            "attributes": attributes,
            "tags": tags,
            "relationships": _sort_relationships(
                [
                    relationship
                    for resource in ranked
                    for relationship in resource.relationships
                ]
            ),
            "warnings": sorted(
                {warning for resource in ranked for warning in resource.warnings}
            ),
        }
    )
    return CloudResource.model_validate(document)


def combine_batches(batches: Sequence[ResourceBatch]) -> ResourceBatch:
    if not batches:
        raise ValueError("at least one Batch is required")

    first = batches[0]
    expected_identity = (first.provider, first.realm, first.account_id)
    if any(
        (batch.provider, batch.realm, batch.account_id) != expected_identity
        for batch in batches
    ):
        raise ValueError("all Batches must share provider, realm, and account")

    candidates_by_uid: dict[str, list[CloudResource]] = defaultdict(list)
    for batch in batches:
        for resource in batch.resources:
            candidates_by_uid[resource.uid].append(resource)

    resources = [
        _merge_resources(candidates_by_uid[uid])
        for uid in sorted(candidates_by_uid)
    ]
    scopes = [
        ResourceScope(
            region=region,
            resource_type=resource_type,
            completeness=Completeness.PARTIAL,
        )
        for region, resource_type in sorted(
            {
                (scope.region, scope.resource_type)
                for batch in batches
                for scope in batch.scopes
            },
            key=lambda item: (item[0], item[1].value),
        )
    ]
    observed_at = max(batch.observed_at for batch in batches)
    parser_profiles = sorted(
        {profile for batch in batches for profile in batch.parser_profiles}
    )
    warnings = sorted({warning for batch in batches for warning in batch.warnings})
    content_hash = hashlib.sha256(
        _canonical_json(
            {"child_batch_hashes": sorted(batch.content_hash for batch in batches)}
        ).encode()
    ).hexdigest()

    return ResourceBatch(
        batch_id=_batch_id(content_hash),
        provider=first.provider,
        realm=first.realm,
        account_id=first.account_id,
        observed_at=observed_at,
        completeness=Completeness.PARTIAL,
        scopes=scopes,
        resources=resources,
        parser_profiles=parser_profiles,
        warnings=warnings,
        content_hash=content_hash,
    )
