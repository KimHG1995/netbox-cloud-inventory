from datetime import UTC, datetime
from pathlib import Path

import pytest

from cloud_inventory.domain.models import Completeness, DetailLevel, ResourceType
from cloud_inventory.ingest.parsers.aws_resource_explorer import (
    AwsResourceExplorerCsvParser,
)
from cloud_inventory.ingest.parsers.base import SourceMetadata
from cloud_inventory.ingest.parsers.registry import build_default_registry


def aws_metadata(export_type: str = "auto") -> SourceMetadata:
    return SourceMetadata(
        provider="aws",
        realm="commercial",
        account_id="123456789012",
        export_type=export_type,
        uploaded_at=datetime(2026, 7, 28, 0, 5, tzinfo=UTC),
        exported_at=datetime(2026, 7, 28, tzinfo=UTC),
        region=None,
    )


def write_csv(path: Path, content: str, *, bom: bool = False) -> Path:
    path.write_text(content, encoding="utf-8-sig" if bom else "utf-8")
    return path


def test_header_detection_is_case_insensitive_and_order_independent(
    tmp_path: Path,
) -> None:
    source = write_csv(
        tmp_path / "renamed.data",
        "aws ACCOUNT,REGION,resource TYPE,identifier\n"
        "123456789012,ap-northeast-2,ec2:instance,i-1\n",
    )

    parser = build_default_registry().detect(source, aws_metadata())

    assert parser.profile_id == "aws.resource_explorer.csv.v1"


def test_detection_accepts_utf8_bom_and_aws_type_form(tmp_path: Path) -> None:
    source = write_csv(
        tmp_path / "export.csv",
        "Identifier,Resource type,Region,AWS account\n"
        "i-1,AWS::EC2::Instance,ap-northeast-2,123456789012\n",
        bom=True,
    )

    batch = AwsResourceExplorerCsvParser().parse(source, aws_metadata())

    vm = next(
        resource
        for resource in batch.resources
        if resource.resource_type is ResourceType.VIRTUAL_MACHINE
    )
    assert vm.external_id == "i-1"


def test_parses_fixture_resources_tags_regions_and_warnings() -> None:
    source = Path("tests/fixtures/aws/resource-explorer.csv")

    batch = AwsResourceExplorerCsvParser().parse(source, aws_metadata())

    vm = next(
        resource
        for resource in batch.resources
        if resource.resource_type is ResourceType.VIRTUAL_MACHINE
    )
    bucket = next(
        resource
        for resource in batch.resources
        if resource.resource_type is ResourceType.OBJECT_BUCKET
    )
    regions = [
        resource
        for resource in batch.resources
        if resource.resource_type is ResourceType.REGION
    ]
    assert vm.external_id == "i-0123456789abcdef0"
    assert vm.name == "poc-web-01"
    assert vm.tags == {"Environment": "test", "Name": "poc-web-01"}
    assert vm.completeness is Completeness.PARTIAL
    assert vm.detail_level is DetailLevel.SUMMARY
    assert bucket.region == "global"
    assert [region.external_id for region in regions] == ["ap-northeast-2"]
    assert batch.warnings == ["unsupported_resource_type:lambda:function"]
    assert len(
        [
            resource
            for resource in batch.resources
            if resource.resource_type is ResourceType.VIRTUAL_MACHINE
        ]
    ) == 1


def test_rejects_aws_account_mismatch(tmp_path: Path) -> None:
    source = write_csv(
        tmp_path / "export.csv",
        "Identifier,Resource type,Region,AWS account\n"
        "i-1,ec2:instance,ap-northeast-2,999999999999\n",
    )

    with pytest.raises(ValueError, match="AWS account"):
        AwsResourceExplorerCsvParser().parse(source, aws_metadata())


def test_arn_is_preserved_and_final_segment_is_name(tmp_path: Path) -> None:
    arn = "arn:aws:rds:ap-northeast-2:123456789012:db:poc-db"
    source = write_csv(
        tmp_path / "export.csv",
        "Identifier,Resource type,Region,AWS account\n"
        f"{arn},rds:db,ap-northeast-2,123456789012\n",
    )

    batch = AwsResourceExplorerCsvParser().parse(source, aws_metadata())
    database = next(
        resource
        for resource in batch.resources
        if resource.resource_type is ResourceType.MANAGED_DATABASE
    )

    assert database.external_id == arn
    assert database.name == "poc-db"


def test_formula_text_is_preserved_and_sensitive_tag_is_omitted(
    tmp_path: Path,
) -> None:
    source = write_csv(
        tmp_path / "export.csv",
        "Identifier,Resource type,Region,AWS account,Description,Access-Key\n"
        "i-1,ec2:instance,ap-northeast-2,123456789012,=1+1,do-not-import\n",
    )

    batch = AwsResourceExplorerCsvParser().parse(source, aws_metadata())
    vm = next(
        resource
        for resource in batch.resources
        if resource.resource_type is ResourceType.VIRTUAL_MACHINE
    )

    assert vm.tags == {"Description": "=1+1"}
    assert vm.warnings == ["sensitive_tag_omitted:Access-Key"]
    assert batch.warnings == vm.warnings


def test_json_and_xlsx_signatures_do_not_match_csv(tmp_path: Path) -> None:
    parser = AwsResourceExplorerCsvParser()
    json_path = tmp_path / "data.csv"
    json_path.write_text('{"Identifier": "i-1"}')
    xlsx_path = tmp_path / "book.csv"
    xlsx_path.write_bytes(b"PK\x03\x04fake")

    assert not parser.detect(json_path, aws_metadata()).matched
    assert not parser.detect(xlsx_path, aws_metadata()).matched
