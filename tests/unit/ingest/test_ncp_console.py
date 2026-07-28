from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from cloud_inventory.domain.models import DetailLevel, ResourceType
from cloud_inventory.ingest.parsers.base import SourceMetadata
from cloud_inventory.ingest.parsers.ncp_console import (
    NcpBucketXlsxParser,
    NcpLoadBalancerXlsxParser,
    NcpPublicIpXlsxParser,
    NcpServerXlsxParser,
)
from cloud_inventory.ingest.parsers.registry import (
    AmbiguousParserError,
    build_default_registry,
)

XlsxWriter = Callable[[Path, list[str], list[list[Any]]], Path]


def ncp_metadata(
    export_type: str = "auto",
    *,
    region: str | None = None,
) -> SourceMetadata:
    return SourceMetadata(
        provider="ncp",
        realm="government",
        account_id="ncp-account-01",
        export_type=export_type,
        uploaded_at=datetime(2026, 7, 28, 0, 5, tzinfo=UTC),
        exported_at=datetime(2026, 7, 28, tzinfo=UTC),
        region=region,
    )


def test_server_parser_supports_korean_headers(
    tmp_path: Path,
    write_xlsx: XlsxWriter,
) -> None:
    source = write_xlsx(
        tmp_path / "servers.xlsx",
        [
            "서버 이름",
            "인스턴스 ID",
            "상태",
            "리전",
            "존",
            "VPC 이름",
            "Subnet 이름",
            "사설 IP",
            "공인 IP",
        ],
        [
            [
                "poc-web-01",
                "server-001",
                "운영중",
                "KR",
                "KR-1",
                "poc-vpc",
                "poc-subnet",
                "192.0.2.10",
                "198.51.100.10",
            ],
            [None] * 9,
        ],
    )

    batch = NcpServerXlsxParser().parse(source, ncp_metadata())

    vm = next(
        resource
        for resource in batch.resources
        if resource.resource_type is ResourceType.VIRTUAL_MACHINE
    )
    ips = [
        resource
        for resource in batch.resources
        if resource.resource_type is ResourceType.IP_ADDRESS
    ]
    region = next(
        resource
        for resource in batch.resources
        if resource.resource_type is ResourceType.REGION
    )
    zone = next(
        resource
        for resource in batch.resources
        if resource.resource_type is ResourceType.ZONE
    )
    assert vm.external_id == "server-001"
    assert vm.attributes == {"subnet": "poc-subnet", "vpc": "poc-vpc"}
    assert vm.detail_level is DetailLevel.SUMMARY
    assert {ip.attributes["address"] for ip in ips} == {
        "192.0.2.10",
        "198.51.100.10",
    }
    assert {ip.external_id for ip in ips} == {
        "server:server-001:private:192.0.2.10",
        "server:server-001:public:198.51.100.10",
    }
    assert region.relationships[0].target_uid == zone.uid


def test_public_ip_parser_supports_english_headers_and_metadata_region(
    tmp_path: Path,
    write_xlsx: XlsxWriter,
) -> None:
    source = write_xlsx(
        tmp_path / "public-ips.xlsx",
        ["Public IP", "Status", "Applied Server", "Private IP", "VPC"],
        [["198.51.100.20", "RUN", "server-001", "192.0.2.10", "poc-vpc"]],
    )

    batch = NcpPublicIpXlsxParser().parse(
        source,
        ncp_metadata(region="KR"),
    )
    public_ip = next(
        resource
        for resource in batch.resources
        if resource.external_id == "public:198.51.100.20"
    )

    assert public_ip.region == "KR"
    assert public_ip.attributes == {
        "address": "198.51.100.20",
        "private_ip": "192.0.2.10",
        "vpc": "poc-vpc",
    }
    assert public_ip.relationships[0].relation_type == "assigned_to"


def test_load_balancer_and_bucket_profiles_parse(
    tmp_path: Path,
    write_xlsx: XlsxWriter,
) -> None:
    load_balancer_source = write_xlsx(
        tmp_path / "load-balancers.xlsx",
        [
            "Load Balancer Name",
            "Instance ID",
            "Status",
            "Type",
            "Network",
            "VPC",
            "Subnet",
            "IP",
            "Region",
        ],
        [
            [
                "poc-lb",
                "lb-001",
                "RUN",
                "APPLICATION",
                "PUBLIC",
                "poc-vpc",
                "poc-subnet",
                "203.0.113.10",
                "KR",
            ]
        ],
    )
    bucket_source = write_xlsx(
        tmp_path / "buckets.xlsx",
        ["Bucket Name", "Region", "Size", "Date Created"],
        [["poc-bucket", "KR", "10 GB", "2026-07-01"]],
    )

    lb_batch = NcpLoadBalancerXlsxParser().parse(
        load_balancer_source,
        ncp_metadata(),
    )
    bucket_batch = NcpBucketXlsxParser().parse(bucket_source, ncp_metadata())
    load_balancer = next(
        resource
        for resource in lb_batch.resources
        if resource.resource_type is ResourceType.LOAD_BALANCER
    )
    bucket = next(
        resource
        for resource in bucket_batch.resources
        if resource.resource_type is ResourceType.OBJECT_BUCKET
    )

    assert load_balancer.attributes == {
        "ip": "203.0.113.10",
        "network": "PUBLIC",
        "subnet": "poc-subnet",
        "type": "APPLICATION",
        "vpc": "poc-vpc",
    }
    assert bucket.external_id == "poc-bucket"
    assert bucket.attributes == {"created_at": "2026-07-01", "size": "10 GB"}


def test_registry_reports_ambiguous_workbook_profile(
    tmp_path: Path,
    write_xlsx: XlsxWriter,
) -> None:
    source = write_xlsx(
        tmp_path / "ambiguous.xlsx",
        ["Server Name", "Load Balancer Name", "Instance ID", "Region"],
        [["server", "load-balancer", "shared-001", "KR"]],
    )

    with pytest.raises(AmbiguousParserError):
        build_default_registry().detect(source, ncp_metadata())


def test_missing_identifier_is_rejected(
    tmp_path: Path,
    write_xlsx: XlsxWriter,
) -> None:
    source = write_xlsx(
        tmp_path / "servers.xlsx",
        ["Server Name", "Instance ID", "Region"],
        [["poc-web-01", None, "KR"]],
    )

    with pytest.raises(ValueError, match="identifier"):
        NcpServerXlsxParser().parse(source, ncp_metadata())


def test_region_is_required(
    tmp_path: Path,
    write_xlsx: XlsxWriter,
) -> None:
    source = write_xlsx(
        tmp_path / "buckets.xlsx",
        ["Bucket Name", "Region", "Date Created"],
        [["poc-bucket", None, "2026-07-01"]],
    )

    with pytest.raises(ValueError, match="region"):
        NcpBucketXlsxParser().parse(source, ncp_metadata())


def test_formula_cells_are_rejected_before_loading(
    tmp_path: Path,
    write_xlsx: XlsxWriter,
) -> None:
    source = write_xlsx(
        tmp_path / "servers.xlsx",
        ["Server Name", "Instance ID", "Region"],
        [["=1+1", "server-001", "KR"]],
    )

    with pytest.raises(ValueError, match="formula"):
        NcpServerXlsxParser().parse(source, ncp_metadata())


def test_external_links_are_rejected(
    tmp_path: Path,
    write_xlsx: XlsxWriter,
) -> None:
    source = write_xlsx(
        tmp_path / "servers.xlsx",
        ["Server Name", "Instance ID", "Region"],
        [["server", "server-001", "KR"]],
    )
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr("xl/externalLinks/externalLink1.xml", "<externalLink/>")

    with pytest.raises(ValueError, match="external link"):
        NcpServerXlsxParser().parse(source, ncp_metadata())


def test_zip_expansion_ratio_is_limited(
    tmp_path: Path,
    write_xlsx: XlsxWriter,
) -> None:
    source = write_xlsx(
        tmp_path / "servers.xlsx",
        ["Server Name", "Instance ID", "Region"],
        [["server", "server-001", "KR"]],
    )
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr("xl/media/highly-compressible.bin", b"0" * 1_000_000)

    with pytest.raises(ValueError, match="expansion ratio"):
        NcpServerXlsxParser().parse(source, ncp_metadata())
