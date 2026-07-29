from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol, Self, cast

import httpx

CHOICE_SET_ENDPOINT = "/api/extras/custom-field-choice-sets/"
CUSTOM_FIELD_ENDPOINT = "/api/extras/custom-fields/"
SCHEMA_PREVIEW_ENDPOINT = "/api/plugins/custom-objects/schema/preview/"
SCHEMA_APPLY_ENDPOINT = "/api/plugins/custom-objects/schema/apply/"

CORE_OBJECT_TYPES = [
    "dcim.region",
    "dcim.site",
    "ipam.vrf",
    "ipam.prefix",
    "ipam.ipaddress",
    "virtualization.virtualmachine",
    "virtualization.vminterface",
]

CHOICE_SETS = {
    "Cloud Provider": [("aws", "aws"), ("ncp", "ncp")],
    "Cloud Realm": [
        ("commercial", "commercial"),
        ("government", "government"),
    ],
    "Collection Source": [("export", "export"), ("api", "api")],
    "Cloud Sync State": [
        ("current", "current"),
        ("warning", "warning"),
        ("error", "error"),
        ("stale_candidate", "stale_candidate"),
        ("inactive", "inactive"),
    ],
}

CORE_FIELDS = [
    {"name": "cloud_uid", "label": "Cloud UID", "type": "text"},
    {
        "name": "provider",
        "label": "Cloud Provider",
        "type": "select",
        "choice_set": "Cloud Provider",
    },
    {
        "name": "realm",
        "label": "Cloud Realm",
        "type": "select",
        "choice_set": "Cloud Realm",
    },
    {"name": "account_id", "label": "Cloud Account ID", "type": "text"},
    {"name": "external_id", "label": "Cloud External ID", "type": "text"},
    {
        "name": "collection_source",
        "label": "Collection Source",
        "type": "select",
        "choice_set": "Collection Source",
    },
    {"name": "cloud_status", "label": "Cloud Status", "type": "text"},
    {"name": "last_seen_at", "label": "Cloud Last Seen", "type": "datetime"},
    {
        "name": "sync_state",
        "label": "Cloud Sync State",
        "type": "select",
        "choice_set": "Cloud Sync State",
    },
    {"name": "source_tags", "label": "Cloud Source Tags", "type": "json"},
    {
        "name": "source_attributes",
        "label": "Cloud Source Attributes",
        "type": "json",
    },
]


class BootstrapClient(Protocol):
    async def list(
        self,
        endpoint: str,
        params: dict[str, str],
    ) -> list[dict[str, Any]]: ...

    async def create(
        self,
        endpoint: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def patch(
        self,
        endpoint: str,
        object_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def post(
        self,
        endpoint: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class BootstrapResult:
    created: int = 0
    changed: int = 0
    unchanged: int = 0

    def __add__(self, other: "BootstrapResult") -> "BootstrapResult":
        return BootstrapResult(
            created=self.created + other.created,
            changed=self.changed + other.changed,
            unchanged=self.unchanged + other.unchanged,
        )


@dataclass(frozen=True)
class SchemaApplyResult:
    created: int
    changed: int
    unchanged: int
    destructive: int


class HttpNetBoxBootstrapClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=60,
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

    async def list(
        self,
        endpoint: str,
        params: dict[str, str],
    ) -> list[dict[str, Any]]:
        response = await self._client.get(endpoint, params=params)
        response.raise_for_status()
        document = response.json()
        if isinstance(document, list):
            return cast(list[dict[str, Any]], document)
        if not isinstance(document, dict):
            raise ValueError("NetBox list response must be a JSON object or array")
        return cast(list[dict[str, Any]], document.get("results", []))

    async def create(
        self,
        endpoint: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._client.post(endpoint, json=payload)
        response.raise_for_status()
        return _response_object(response)

    async def patch(
        self,
        endpoint: str,
        object_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._client.patch(
            f"{endpoint}{object_id}/",
            json=payload,
        )
        response.raise_for_status()
        return _response_object(response)

    async def post(
        self,
        endpoint: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._client.post(endpoint, json=payload)
        response.raise_for_status()
        return _response_object(response)


def _response_object(response: httpx.Response) -> dict[str, Any]:
    document = response.json()
    if not isinstance(document, dict):
        raise ValueError("NetBox response must be a JSON object")
    return cast(dict[str, Any], document)


def _normalized_choices(raw_choices: object) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if not isinstance(raw_choices, list):
        return normalized
    for item in raw_choices:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            normalized[str(item[0])] = str(item[1])
        elif isinstance(item, dict) and "value" in item:
            normalized[str(item["value"])] = str(item.get("label", item["value"]))
    return normalized


async def _ensure_choice_set(
    client: BootstrapClient,
    name: str,
    choices: list[tuple[str, str]],
) -> tuple[dict[str, Any], BootstrapResult]:
    existing = await client.list(CHOICE_SET_ENDPOINT, {"name": name})
    if len(existing) > 1:
        raise ValueError(f"multiple Custom Field Choice Sets named {name}")
    payload = {
        "name": name,
        "extra_choices": [list(choice) for choice in choices],
        "order_alphabetically": False,
    }
    if not existing:
        return await client.create(CHOICE_SET_ENDPOINT, payload), BootstrapResult(
            created=1
        )

    choice_set = existing[0]
    current_choices = _normalized_choices(
        choice_set.get("extra_choices", choice_set.get("choices", []))
    )
    desired_choices = dict(choices)
    for value, label in desired_choices.items():
        if value in current_choices and current_choices[value] != label:
            raise ValueError(f"conflicting choice value {value} in {name}")

    merged_choices = {**current_choices, **desired_choices}
    if merged_choices != current_choices:
        updated = await client.patch(
            CHOICE_SET_ENDPOINT,
            int(choice_set["id"]),
            {
                "extra_choices": [
                    [value, label] for value, label in merged_choices.items()
                ]
            },
        )
        return updated, BootstrapResult(changed=1)
    return choice_set, BootstrapResult(unchanged=1)


def _choice_set_identity(value: object) -> tuple[int | None, str | None]:
    if isinstance(value, int):
        return value, None
    if isinstance(value, dict):
        object_id = value.get("id")
        name = value.get("name")
        return (
            int(object_id) if object_id is not None else None,
            str(name) if name is not None else None,
        )
    return None, None


def _choice_value(value: object) -> object:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


async def _ensure_core_field(
    client: BootstrapClient,
    definition: dict[str, Any],
    choice_sets: dict[str, dict[str, Any]],
) -> BootstrapResult:
    name = str(definition["name"])
    existing = await client.list(CUSTOM_FIELD_ENDPOINT, {"name": name})
    if len(existing) > 1:
        raise ValueError(f"multiple Core Custom Fields named {name}")

    payload = {
        "name": name,
        "label": definition["label"],
        "type": definition["type"],
        "object_types": CORE_OBJECT_TYPES,
        "required": False,
        "ui_editable": "no",
    }
    choice_set_name = definition.get("choice_set")
    if choice_set_name is not None:
        payload["choice_set"] = int(choice_sets[str(choice_set_name)]["id"])

    if not existing:
        await client.create(CUSTOM_FIELD_ENDPOINT, payload)
        return BootstrapResult(created=1)

    custom_field = existing[0]
    if _choice_value(custom_field.get("type")) != definition["type"]:
        raise ValueError(f"conflicting type for Core Custom Field {name}")
    if custom_field.get("required") is True:
        raise ValueError(f"cannot make required Core Custom Field {name} optional")

    patch: dict[str, Any] = {}
    current_object_types = {
        str(item.get("value", item.get("model")))
        if isinstance(item, dict)
        else str(item)
        for item in custom_field.get("object_types", [])
    }
    desired_object_types = set(CORE_OBJECT_TYPES)
    if not desired_object_types.issubset(current_object_types):
        patch["object_types"] = sorted(current_object_types | desired_object_types)
    if _choice_value(custom_field.get("ui_editable")) != "no":
        patch["ui_editable"] = "no"

    if choice_set_name is not None:
        desired_choice_set = choice_sets[str(choice_set_name)]
        current_id, current_name = _choice_set_identity(custom_field.get("choice_set"))
        desired_id = int(desired_choice_set["id"])
        if current_id is None and current_name is None:
            patch["choice_set"] = desired_id
        elif current_id != desired_id and current_name != choice_set_name:
            raise ValueError(f"conflicting Choice Set for Core Custom Field {name}")

    if patch:
        await client.patch(CUSTOM_FIELD_ENDPOINT, int(custom_field["id"]), patch)
        return BootstrapResult(changed=1)
    return BootstrapResult(unchanged=1)


async def bootstrap_core_fields(client: BootstrapClient) -> BootstrapResult:
    result = BootstrapResult()
    choice_sets: dict[str, dict[str, Any]] = {}
    for name, choices in CHOICE_SETS.items():
        choice_set, choice_result = await _ensure_choice_set(client, name, choices)
        choice_sets[name] = choice_set
        result += choice_result
    for definition in CORE_FIELDS:
        result += await _ensure_core_field(client, definition, choice_sets)
    return result


async def apply_custom_object_schema(
    client: BootstrapClient,
    schema_document: dict[str, Any],
) -> SchemaApplyResult:
    preview = await client.post(SCHEMA_PREVIEW_ENDPOINT, schema_document)
    diffs = list(preview.get("diffs", []))
    destructive = sum(
        1 for item in diffs if item.get("has_destructive_changes") is True
    )
    if destructive:
        raise ValueError("portable schema preview contains destructive changes")

    response = await client.post(
        SCHEMA_APPLY_ENDPOINT,
        {"allow_destructive": False, "schema": schema_document},
    )
    if response.get("applied") is not True:
        raise ValueError("NetBox did not confirm portable schema application")

    return SchemaApplyResult(
        created=sum(1 for item in diffs if item.get("is_new") is True),
        changed=sum(
            1
            for item in diffs
            if item.get("is_new") is not True and item.get("has_changes") is True
        ),
        unchanged=sum(1 for item in diffs if item.get("has_changes") is not True),
        destructive=destructive,
    )
