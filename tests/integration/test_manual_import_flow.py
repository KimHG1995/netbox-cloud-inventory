import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from openpyxl import Workbook


def _required_environment() -> tuple[str, str, str]:
    if os.getenv("RUN_MANUAL_IMPORT_E2E") != "1":
        pytest.skip("set RUN_MANUAL_IMPORT_E2E=1 for the full Compose test")
    return (
        os.getenv("INVENTORY_API_URL", "http://127.0.0.1:8080").rstrip("/"),
        os.getenv("INVENTORY_NETBOX_URL", "http://127.0.0.1:8000").rstrip("/"),
        os.environ["INVENTORY_NETBOX_TOKEN"],
    )


def _wait_for_json(
    client: httpx.Client,
    path: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_document: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(path)
        if response.status_code == 200:
            document = response.json()
            if isinstance(document, dict):
                last_document = document
                if predicate(document):
                    return document
        time.sleep(0.25)
    raise AssertionError(f"timed out waiting for {path}: {last_document}")


def _get_or_create(
    client: httpx.Client,
    endpoint: str,
    filters: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = client.get(endpoint, params=filters)
    response.raise_for_status()
    results = response.json()["results"]
    if results:
        return results[0]
    response = client.post(endpoint, json=payload)
    response.raise_for_status()
    return response.json()


def _upload(
    client: httpx.Client,
    *,
    filename: str,
    content: bytes,
    media_type: str,
    provider: str,
    realm: str,
    account_id: str,
    export_type: str,
    exported_at: datetime,
    region: str | None,
) -> tuple[httpx.Response, dict[str, Any]]:
    response = client.post(
        "/imports",
        data={
            "provider": provider,
            "realm": realm,
            "account_id": account_id,
            "export_type": export_type,
            "exported_at": exported_at.isoformat(),
            **({"region": region} if region else {}),
        },
        files={"files": (filename, content, media_type)},
    )
    assert response.status_code in {200, 202}, response.text
    return response, response.json()


def _preview_and_apply(
    client: httpx.Client,
    import_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    preview = _wait_for_json(
        client,
        f"/imports/{import_id}/preview",
        lambda document: "batch_hash" in document,
    )
    response = client.post(
        f"/imports/{import_id}/apply",
        json={
            "batch_hash": preview["batch_hash"],
            "apply_valid_only": False,
        },
    )
    assert response.status_code in {200, 202}, response.text
    run_id = response.json()["run_id"]
    run = _wait_for_json(
        client,
        f"/runs/{run_id}",
        lambda document: document.get("status") in {"succeeded", "failed"},
    )
    assert run["status"] == "succeeded", run
    assert run["summary"].get("error", 0) == 0, run
    return preview, run


def _find_by_cloud_uid(
    client: httpx.Client,
    endpoint: str,
    cloud_uid: str,
    *,
    core: bool = False,
) -> dict[str, Any]:
    response = client.get(
        endpoint,
        params={"cf_cloud_uid" if core else "cloud_uid": cloud_uid},
    )
    response.raise_for_status()
    results = response.json()["results"]
    assert len(results) == 1
    detail = client.get(f"{endpoint}{results[0]['id']}/")
    detail.raise_for_status()
    return detail.json()


def _xlsx_bytes(
    tmp_path: Path,
    server_id: str,
    private_ip: str = "192.0.2.20",
) -> bytes:
    path = tmp_path / f"{server_id}.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "Server Name",
            "Instance ID",
            "Status",
            "Region",
            "Zone",
            "VPC",
            "Subnet",
            "Private IP",
        ]
    )
    sheet.append(
        [
            f"server-{server_id}",
            server_id,
            "RUN",
            "KR",
            "KR-1",
            "poc-vpc",
            "poc-subnet",
            private_ip,
        ]
    )
    workbook.save(path)
    workbook.close()
    return path.read_bytes()


def _replace_account(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_replace_account(item, old, new) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_account(item, old, new)
            for key, item in value.items()
        }
    return value


def _choice_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("value")
    return value


def test_manual_import_flow_is_idempotent_and_preserves_manual_owner(
    tmp_path: Path,
) -> None:
    inventory_url, netbox_url, token = _required_environment()
    suffix = uuid4().hex[:8]
    aws_account = f"9{int(suffix, 16) % 100_000_000_000:011d}"
    ncp_account = f"ncp-e2e-{suffix}"
    bundle_account = f"bundle-{suffix}"
    bucket_name = f"poc-e2e-{suffix}"
    service_code = f"payments-{suffix}"
    ip_seed = int(suffix, 16)
    first_private_ip = f"198.18.{(ip_seed >> 8) % 256}.{ip_seed % 253 + 1}"
    second_private_ip = (
        f"198.19.{(ip_seed >> 16) % 256}.{(ip_seed + 1) % 253 + 1}"
    )
    now = datetime.now(UTC) - timedelta(minutes=1)
    netbox_headers = {"Authorization": f"Bearer {token}"}

    with (
        httpx.Client(base_url=inventory_url, timeout=30) as inventory,
        httpx.Client(
            base_url=netbox_url,
            headers=netbox_headers,
            timeout=30,
        ) as netbox,
    ):
        platform = _get_or_create(
            netbox,
            "/api/users/owners/",
            {"name": "platform"},
            {
                "name": "platform",
                "group": None,
                "users": [],
                "user_groups": [],
            },
        )
        manual_override = _get_or_create(
            netbox,
            "/api/users/owners/",
            {"name": "manual-override"},
            {
                "name": "manual-override",
                "group": None,
                "users": [],
                "user_groups": [],
            },
        )
        service = _get_or_create(
            netbox,
            "/api/plugins/custom-objects/business-service/",
            {"service_code": service_code},
            {"name": f"Payments {suffix}", "service_code": service_code},
        )
        tag_mapping_id = uuid4()
        owner_mapping_id = uuid4()
        assert inventory.put(
            f"/mappings/tags/{tag_mapping_id}",
            json={
                "provider": "aws",
                "realm": "commercial",
                "account_id": aws_account,
                "source_key": "Service",
                "source_value": service_code,
                "business_service_code": service_code,
                "priority": 100,
                "enabled": True,
            },
        ).status_code in {200, 201}
        assert inventory.put(
            f"/mappings/owners/{owner_mapping_id}",
            json={
                "provider": "aws",
                "realm": "commercial",
                "account_id": aws_account,
                "source_value": "platform",
                "netbox_owner_id": platform["id"],
                "priority": 100,
                "enabled": True,
            },
        ).status_code in {200, 201}

        aws_csv = (
            "Identifier,Resource type,Region,AWS account,Total tags,"
            "Name,Service,Owner\n"
            f"i-{suffix},ec2:instance,ap-northeast-2,{aws_account},"
            f"3,web-{suffix},{service_code},platform\n"
            f"{bucket_name},s3:bucket,,{aws_account},2,,"
            f"{service_code},platform\n"
        ).encode()
        first_response, first_import = _upload(
            inventory,
            filename="aws.csv",
            content=aws_csv,
            media_type="text/csv",
            provider="aws",
            realm="commercial",
            account_id=aws_account,
            export_type="aws.resource_explorer.csv.v1",
            exported_at=now,
            region="ap-northeast-2",
        )
        assert first_response.status_code == 202
        aws_preview, aws_run = _preview_and_apply(
            inventory,
            first_import["import_id"],
        )
        assert aws_preview["summary"]["warning"] == 1
        assert aws_run["summary"]["create"] >= 3

        bucket_uid = (
            f"aws:commercial:{aws_account}:global:object_bucket:{bucket_name}"
        )
        bucket = _find_by_cloud_uid(
            netbox,
            "/api/plugins/custom-objects/object-bucket/",
            bucket_uid,
        )
        assert bucket["owner"]["id"] == platform["id"]
        service_detail = netbox.get(
            f"/api/plugins/custom-objects/business-service/{service['id']}/"
        )
        service_detail.raise_for_status()
        assert str(bucket["id"]) in json.dumps(service_detail.json()["resources"])

        duplicate_response, duplicate_import = _upload(
            inventory,
            filename="aws.csv",
            content=aws_csv,
            media_type="text/csv",
            provider="aws",
            realm="commercial",
            account_id=aws_account,
            export_type="aws.resource_explorer.csv.v1",
            exported_at=now,
            region="ap-northeast-2",
        )
        assert duplicate_response.status_code == 200
        assert duplicate_import == first_import
        repeated_apply = inventory.post(
            f"/imports/{first_import['import_id']}/apply",
            json={
                "batch_hash": aws_preview["batch_hash"],
                "apply_valid_only": False,
            },
        )
        assert repeated_apply.status_code == 200
        assert repeated_apply.json()["run_id"] == aws_run["id"]

        override = netbox.patch(
            f"/api/plugins/custom-objects/object-bucket/{bucket['id']}/",
            json={"owner": manual_override["id"]},
        )
        override.raise_for_status()
        later_csv = aws_csv.replace(
            b",Name,Service,Owner\n",
            b",Name,Service,Owner,Revision\n",
        ).replace(b",platform\n", b",platform,2\n")
        _, later_import = _upload(
            inventory,
            filename="aws-later.csv",
            content=later_csv,
            media_type="text/csv",
            provider="aws",
            realm="commercial",
            account_id=aws_account,
            export_type="aws.resource_explorer.csv.v1",
            exported_at=now + timedelta(seconds=1),
            region="ap-northeast-2",
        )
        _, later_run = _preview_and_apply(inventory, later_import["import_id"])
        assert later_run["summary"]["warning"] >= 1
        bucket_after_override = _find_by_cloud_uid(
            netbox,
            "/api/plugins/custom-objects/object-bucket/",
            bucket_uid,
        )
        assert bucket_after_override["owner"]["id"] == manual_override["id"]

        ncp_content = _xlsx_bytes(
            tmp_path,
            f"server-{suffix}",
            first_private_ip,
        )
        _, ncp_import = _upload(
            inventory,
            filename="servers.xlsx",
            content=ncp_content,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            provider="ncp",
            realm="government",
            account_id=ncp_account,
            export_type="ncp.server_list.xlsx.v1",
            exported_at=now,
            region="KR",
        )
        _, ncp_run = _preview_and_apply(inventory, ncp_import["import_id"])
        assert ncp_run["summary"]["create"] >= 4
        ncp_vm_uid = (
            f"ncp:government:{ncp_account}:KR:virtual_machine:server-{suffix}"
        )
        ncp_vm = _find_by_cloud_uid(
            netbox,
            "/api/virtualization/virtual-machines/",
            ncp_vm_uid,
            core=True,
        )
        assert _choice_value(ncp_vm["custom_fields"]["sync_state"]) == "current"

        omitted_content = _xlsx_bytes(
            tmp_path,
            f"replacement-{suffix}",
            second_private_ip,
        )
        _, omission_import = _upload(
            inventory,
            filename="replacement.xlsx",
            content=omitted_content,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            provider="ncp",
            realm="government",
            account_id=ncp_account,
            export_type="ncp.server_list.xlsx.v1",
            exported_at=now + timedelta(seconds=2),
            region="KR",
        )
        _preview_and_apply(inventory, omission_import["import_id"])
        original_after_omission = _find_by_cloud_uid(
            netbox,
            "/api/virtualization/virtual-machines/",
            ncp_vm_uid,
            core=True,
        )
        assert (
            _choice_value(
                original_after_omission["custom_fields"]["sync_state"]
            )
            == "current"
        )

        bundle = json.loads(
            Path("tests/fixtures/import-bundle/full-inventory.json").read_text()
        )
        bundle = _replace_account(bundle, "123456789012", bundle_account)
        bundle_network = f"10.{(ip_seed >> 8) % 256}.{ip_seed % 256}"
        bundle = _replace_account(bundle, "192.0.2", bundle_network)
        bundle["exported_at"] = now.isoformat().replace("+00:00", "Z")
        bundle_content = json.dumps(bundle).encode()
        _, bundle_import = _upload(
            inventory,
            filename="full-inventory.json",
            content=bundle_content,
            media_type="application/json",
            provider="aws",
            realm="commercial",
            account_id=bundle_account,
            export_type="canonical.import_bundle.v1",
            exported_at=now,
            region=None,
        )
        _, bundle_run = _preview_and_apply(
            inventory,
            bundle_import["import_id"],
        )
        assert bundle_run["summary"]["create"] >= 10
