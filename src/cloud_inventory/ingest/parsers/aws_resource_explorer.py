import csv
from collections import defaultdict
from pathlib import Path

from cloud_inventory.domain.models import (
    CloudResource,
    Completeness,
    DetailLevel,
    ResourceBatch,
    ResourceType,
    is_sensitive_key,
)
from cloud_inventory.domain.uid import build_cloud_uid
from cloud_inventory.ingest.batch import canonical_resource_sha256, finalize_batch
from cloud_inventory.ingest.parsers.base import DetectionResult, SourceMetadata

AWS_TYPE_MAP = {
    "ec2:instance": ResourceType.VIRTUAL_MACHINE,
    "ec2:networkinterface": ResourceType.NETWORK_INTERFACE,
    "ec2:vpc": ResourceType.VPC,
    "ec2:subnet": ResourceType.SUBNET,
    "ec2:elasticip": ResourceType.IP_ADDRESS,
    "elasticloadbalancing:loadbalancer": ResourceType.LOAD_BALANCER,
    "elasticloadbalancingv2:loadbalancer": ResourceType.LOAD_BALANCER,
    "route53:hostedzone": ResourceType.DNS_ZONE,
    "route53domains:domain": ResourceType.DOMAIN,
    "rds:db": ResourceType.MANAGED_DATABASE,
    "rds:dbinstance": ResourceType.MANAGED_DATABASE,
    "rds:cluster": ResourceType.MANAGED_DATABASE,
    "rds:dbcluster": ResourceType.MANAGED_DATABASE,
    "s3:bucket": ResourceType.OBJECT_BUCKET,
}

_BASE_HEADERS = {
    "identifier": "identifier",
    "resourcetype": "resource_type",
    "region": "region",
    "awsaccount": "aws_account",
    "totaltags": "total_tags",
}
_REQUIRED_BASE_KEYS = {"identifier", "resource_type", "region", "aws_account"}
_REQUIRED_HEADER_NAMES = [
    "AWS account",
    "Identifier",
    "Region",
    "Resource type",
]


def _normalize(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _normalized_resource_type(value: str) -> str | None:
    if value.casefold().startswith("aws::"):
        segments = value.split("::")
        if len(segments) < 3:
            return None
        service, resource_type = segments[1], segments[-1]
    else:
        segments = value.split(":", maxsplit=1)
        if len(segments) != 2:
            return None
        service, resource_type = segments
    return f"{_normalize(service)}:{_normalize(resource_type)}"


def _header_contract(headers: list[str]) -> dict[str, int]:
    contract: dict[str, int] = {}
    for index, header in enumerate(headers):
        base_key = _BASE_HEADERS.get(_normalize(header))
        if base_key is not None:
            contract[base_key] = index
    return contract


def _header_diagnostic(headers: list[str]) -> str:
    received = ", ".join(header.strip() for header in headers)
    required = ", ".join(_REQUIRED_HEADER_NAMES)
    return (
        f"received headers=[{received}]; "
        f"required headers=[{required}]"
    )


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        try:
            headers = next(reader)
        except StopIteration as error:
            raise ValueError("AWS Resource Explorer CSV is empty") from error
        return headers, list(reader)


def _cell(row: list[str], index: int) -> str:
    return row[index] if index < len(row) else ""


def _arn_final_segment(identifier: str) -> str:
    return identifier.rsplit("/", maxsplit=1)[-1].rsplit(":", maxsplit=1)[-1]


class AwsResourceExplorerCsvParser:
    profile_id = "aws.resource_explorer.csv.v1"
    schema_version = "1"
    source_priority = 100

    def detect(self, path: Path, metadata: SourceMetadata) -> DetectionResult:
        del metadata
        try:
            prefix = path.read_bytes()[:4096]
        except OSError:
            return DetectionResult(False, 0, "file cannot be read")

        without_bom = prefix.removeprefix(b"\xef\xbb\xbf").lstrip()
        if without_bom.startswith((b"{", b"[", b"PK\x03\x04")):
            return DetectionResult(False, 0, "JSON and ZIP signatures are not CSV")

        try:
            headers, _ = _read_csv(path)
        except (UnicodeDecodeError, csv.Error, ValueError):
            return DetectionResult(False, 0, "content is not a supported CSV")

        contract = _header_contract(headers)
        matched = _REQUIRED_BASE_KEYS.issubset(contract)
        return DetectionResult(
            matched=matched,
            confidence=100 if matched else 0,
            reason="AWS Resource Explorer headers matched"
            if matched
            else _header_diagnostic(headers),
        )

    def parse(self, path: Path, metadata: SourceMetadata) -> ResourceBatch:
        headers, rows = _read_csv(path)
        contract = _header_contract(headers)
        if not _REQUIRED_BASE_KEYS.issubset(contract):
            raise ValueError(_header_diagnostic(headers))

        tag_columns = [
            (index, header.strip())
            for index, header in enumerate(headers)
            if _BASE_HEADERS.get(_normalize(header)) is None and header.strip()
        ]
        resources: list[CloudResource] = []
        regions: set[str] = set()
        warnings: set[str] = set()

        for row in rows:
            if not any(cell.strip() for cell in row):
                continue

            identifier = _cell(row, contract["identifier"]).strip()
            raw_resource_type = _cell(row, contract["resource_type"]).strip()
            region = _cell(row, contract["region"]).strip() or "global"
            account_id = _cell(row, contract["aws_account"]).strip()
            if account_id != metadata.account_id:
                raise ValueError(
                    f"AWS account mismatch: expected {metadata.account_id}, got {account_id}"
                )
            if not identifier:
                raise ValueError("AWS Resource Explorer row is missing Identifier")

            normalized_type = _normalized_resource_type(raw_resource_type)
            resource_type = (
                AWS_TYPE_MAP.get(normalized_type)
                if normalized_type is not None
                else None
            )
            if resource_type is None:
                warnings.add(f"unsupported_resource_type:{raw_resource_type}")
                continue

            tags: dict[str, str] = {}
            resource_warnings: list[str] = []
            for index, tag_key in tag_columns:
                value = _cell(row, index)
                if not value.strip():
                    continue
                if is_sensitive_key(tag_key):
                    resource_warnings.append(f"sensitive_tag_omitted:{tag_key}")
                    continue
                tags[tag_key] = value

            name_tag = next(
                (
                    value
                    for key, value in tags.items()
                    if _normalize(key) == "name" and value
                ),
                None,
            )
            name = (
                name_tag
                or (_arn_final_segment(identifier) if identifier.startswith("arn:") else None)
                or identifier
            )
            resources.append(
                CloudResource(
                    uid=build_cloud_uid(
                        metadata.provider,
                        metadata.realm,
                        metadata.account_id,
                        region,
                        resource_type,
                        identifier,
                    ),
                    provider=metadata.provider,
                    realm=metadata.realm,
                    account_id=metadata.account_id,
                    region=region,
                    resource_type=resource_type,
                    external_id=identifier,
                    name=name,
                    tags=tags,
                    observed_at=metadata.exported_at,
                    warnings=resource_warnings,
                    completeness=Completeness.PARTIAL,
                    detail_level=DetailLevel.SUMMARY,
                    source_profile=self.profile_id,
                    source_priority=self.source_priority,
                )
            )
            if region != "global":
                regions.add(region)

        for region in sorted(regions):
            resources.append(
                CloudResource(
                    uid=build_cloud_uid(
                        metadata.provider,
                        metadata.realm,
                        metadata.account_id,
                        region,
                        ResourceType.REGION,
                        region,
                    ),
                    provider=metadata.provider,
                    realm=metadata.realm,
                    account_id=metadata.account_id,
                    region=region,
                    resource_type=ResourceType.REGION,
                    external_id=region,
                    name=region,
                    observed_at=metadata.exported_at,
                    completeness=Completeness.PARTIAL,
                    detail_level=DetailLevel.SUMMARY,
                    source_profile=self.profile_id,
                    source_priority=self.source_priority,
                )
            )

        candidates_by_uid: dict[str, list[CloudResource]] = defaultdict(list)
        for resource in resources:
            candidates_by_uid[resource.uid].append(resource)
        deduplicated = [
            max(candidates, key=canonical_resource_sha256)
            for _, candidates in sorted(candidates_by_uid.items())
        ]

        return finalize_batch(
            provider=metadata.provider,
            realm=metadata.realm,
            account_id=metadata.account_id,
            observed_at=metadata.exported_at,
            resources=deduplicated,
            parser_profile=self.profile_id,
            source_priority=self.source_priority,
            detail_level=DetailLevel.SUMMARY,
            warnings=sorted(warnings),
        )
