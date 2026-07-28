from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)


class Provider(StrEnum):
    AWS = "aws"
    NCP = "ncp"


class Realm(StrEnum):
    COMMERCIAL = "commercial"
    GOVERNMENT = "government"


class ResourceType(StrEnum):
    CLOUD_ACCOUNT = "cloud_account"
    REGION = "region"
    ZONE = "zone"
    VPC = "vpc"
    SUBNET = "subnet"
    VIRTUAL_MACHINE = "virtual_machine"
    NETWORK_INTERFACE = "network_interface"
    IP_ADDRESS = "ip_address"
    LOAD_BALANCER = "load_balancer"
    DOMAIN = "domain"
    DNS_ZONE = "dns_zone"
    DNS_RECORD = "dns_record"
    MANAGED_DATABASE = "managed_database"
    OBJECT_BUCKET = "object_bucket"


class Completeness(StrEnum):
    FULL = "full"
    PARTIAL = "partial"


class DetailLevel(StrEnum):
    SUMMARY = "summary"
    DETAILED = "detailed"


class Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_type: Literal[
        "contains",
        "attached_to",
        "assigned_to",
        "resolves_to",
        "routes_to",
        "serves",
    ]
    target_uid: str


_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "accesskey",
        "secretkey",
        "privatekey",
        "credential",
        "connectionstring",
    }
)


def normalize_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def is_sensitive_key(key: str) -> bool:
    return normalize_key(key) in _SENSITIVE_KEYS


def _find_sensitive_attribute_key(value: JsonValue) -> str | None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if is_sensitive_key(key):
                return key
            found = _find_sensitive_attribute_key(nested_value)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested_value in value:
            found = _find_sensitive_attribute_key(nested_value)
            if found is not None:
                return found
    return None


class ResourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    uid: str
    provider: Provider
    realm: Realm
    account_id: str
    region: str
    resource_type: ResourceType
    external_id: str
    name: str
    status: str = "unknown"
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    relationships: list[Relationship] = Field(default_factory=list)
    observed_at: AwareDatetime
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_sensitive_keys(self) -> Self:
        sensitive_attribute = _find_sensitive_attribute_key(self.attributes)
        if sensitive_attribute is not None:
            raise ValueError(f"sensitive attribute key is not allowed: {sensitive_attribute}")

        sensitive_tag = next((key for key in self.tags if is_sensitive_key(key)), None)
        if sensitive_tag is not None:
            raise ValueError(f"sensitive tag key is not allowed: {sensitive_tag}")
        return self


class ImportResource(ResourceDocument):
    model_config = ConfigDict(extra="forbid")


class CloudResource(ResourceDocument):
    model_config = ConfigDict(extra="forbid")

    source: Literal["export"] = "export"
    completeness: Completeness
    detail_level: DetailLevel
    source_profile: str
    source_priority: int = Field(ge=0, le=1000)

    @model_validator(mode="after")
    def require_partial_export(self) -> Self:
        if self.source == "export" and self.completeness is not Completeness.PARTIAL:
            raise ValueError("export resources must use partial completeness")
        return self


class ResourceScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str
    resource_type: ResourceType
    completeness: Completeness


class ResourceBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    batch_id: UUID
    provider: Provider
    realm: Realm
    account_id: str
    observed_at: AwareDatetime
    completeness: Completeness
    scopes: list[ResourceScope]
    resources: list[CloudResource]
    parser_profiles: list[str]
    warnings: list[str] = Field(default_factory=list)
    content_hash: str


class ImportBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"]
    provider: Provider
    realm: Realm
    account_id: str
    exported_at: AwareDatetime
    resources: list[ImportResource]

    @model_validator(mode="after")
    def validate_resources(self) -> Self:
        resource_uids = [resource.uid for resource in self.resources]
        if len(resource_uids) != len(set(resource_uids)):
            raise ValueError("duplicate resource uid")

        known_uids = set(resource_uids)
        for resource in self.resources:
            identity = (resource.provider, resource.realm, resource.account_id)
            bundle_identity = (self.provider, self.realm, self.account_id)
            if identity != bundle_identity:
                raise ValueError(
                    "resource provider, realm, and account must match the Import Bundle"
                )

            for relationship in resource.relationships:
                if relationship.target_uid in known_uids:
                    continue
                warning = f"unresolved_relation:{relationship.target_uid}"
                if warning not in resource.warnings:
                    resource.warnings.append(warning)
        return self
