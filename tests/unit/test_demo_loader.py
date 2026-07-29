from pathlib import Path
from typing import Any

import httpx
import pytest

from cloud_inventory.demo_loader import (
    DemoLoadError,
    DemoSource,
    default_demo_sources,
    load_demo_source,
)

BATCH_HASH = "a" * 64


def _demo_source(path: Path) -> DemoSource:
    return DemoSource(
        name="AWS demo",
        path=path,
        media_type="text/csv",
        provider="aws",
        realm="commercial",
        account_id="123456789012",
        export_type="aws.resource_explorer.csv.v1",
        exported_at="2026-07-28T00:00:00+00:00",
        region="ap-northeast-2",
    )


def _preview_document(*, errors: int = 0) -> dict[str, Any]:
    return {
        "import_id": "import-1",
        "batch_hash": BATCH_HASH,
        "expires_at": "2026-08-27T00:00:00+00:00",
        "summary": {"create": 1, "update": 0, "error": errors},
        "total_changes": 1,
        "offset": 0,
        "limit": 100,
        "changes": [],
    }


def _run_document(status: str) -> dict[str, Any]:
    return {
        "id": "run-1",
        "import_id": "import-1",
        "batch_hash": BATCH_HASH,
        "apply_valid_only": False,
        "status": status,
        "checkpoint": None,
        "summary": {"create": 1, "update": 0, "error": 0},
        "started_at": "2026-07-28T00:00:01+00:00",
        "finished_at": (
            "2026-07-28T00:00:02+00:00"
            if status in {"succeeded", "failed"}
            else None
        ),
    }


def _write_csv(path: Path) -> None:
    path.write_text(
        "Identifier,Resource type,Region,AWS account\n"
        "bucket,s3:bucket,,123456789012\n",
        encoding="utf-8",
    )


def test_load_demo_source_uploads_previews_applies_and_waits_for_success(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "aws.csv"
    _write_csv(source_path)
    calls: list[str] = []
    run_polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal run_polls
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "POST" and request.url.path == "/imports":
            assert b'name="provider"' in request.content
            assert b"123456789012" in request.content
            assert b"bucket,s3:bucket" in request.content
            return httpx.Response(
                202,
                json={"import_id": "import-1", "parse_job_id": "parse-1"},
            )
        if request.method == "GET" and request.url.path.endswith("/preview"):
            return httpx.Response(200, json=_preview_document())
        if request.method == "POST" and request.url.path.endswith("/apply"):
            assert request.content == (
                b'{"batch_hash":"' + BATCH_HASH.encode() + b'",'
                b'"apply_valid_only":false}'
            )
            return httpx.Response(202, json={"run_id": "run-1"})
        run_polls += 1
        status = "running" if run_polls == 1 else "succeeded"
        return httpx.Response(200, json=_run_document(status))

    with httpx.Client(
        base_url="http://inventory.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = load_demo_source(
            client,
            _demo_source(source_path),
            sleep=lambda _: None,
        )

    assert result.import_id == "import-1"
    assert result.run_id == "run-1"
    assert result.summary == {"create": 1, "update": 0, "error": 0}
    assert calls == [
        "POST /imports",
        "GET /imports/import-1/preview",
        "POST /imports/import-1/apply",
        "GET /runs/run-1",
        "GET /runs/run-1",
    ]


def test_load_demo_source_retries_preview_not_ready(tmp_path: Path) -> None:
    source_path = tmp_path / "aws.csv"
    _write_csv(source_path)
    preview_polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal preview_polls
        if request.url.path == "/imports":
            return httpx.Response(
                200,
                json={"import_id": "import-1", "parse_job_id": "parse-1"},
            )
        if request.url.path.endswith("/preview"):
            preview_polls += 1
            if preview_polls == 1:
                return httpx.Response(
                    409,
                    json={
                        "detail": {
                            "code": "preview_not_ready",
                            "message": "preview is not ready",
                        }
                    },
                )
            return httpx.Response(200, json=_preview_document())
        if request.url.path.endswith("/apply"):
            return httpx.Response(200, json={"run_id": "run-1"})
        return httpx.Response(200, json=_run_document("succeeded"))

    with httpx.Client(
        base_url="http://inventory.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        load_demo_source(
            client,
            _demo_source(source_path),
            sleep=lambda _: None,
        )

    assert preview_polls == 2


def test_load_demo_source_rejects_preview_errors(tmp_path: Path) -> None:
    source_path = tmp_path / "aws.csv"
    _write_csv(source_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/imports":
            return httpx.Response(
                202,
                json={"import_id": "import-1", "parse_job_id": "parse-1"},
            )
        return httpx.Response(200, json=_preview_document(errors=1))

    with httpx.Client(
        base_url="http://inventory.test",
        transport=httpx.MockTransport(handler),
    ) as client, pytest.raises(
        DemoLoadError,
        match="preview contains 1 error",
    ):
        load_demo_source(
            client,
            _demo_source(source_path),
            sleep=lambda _: None,
        )


def test_load_demo_source_rejects_failed_run(tmp_path: Path) -> None:
    source_path = tmp_path / "aws.csv"
    _write_csv(source_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/imports":
            return httpx.Response(
                202,
                json={"import_id": "import-1", "parse_job_id": "parse-1"},
            )
        if request.url.path.endswith("/preview"):
            return httpx.Response(200, json=_preview_document())
        if request.url.path.endswith("/apply"):
            return httpx.Response(202, json={"run_id": "run-1"})
        return httpx.Response(200, json=_run_document("failed"))

    with httpx.Client(
        base_url="http://inventory.test",
        transport=httpx.MockTransport(handler),
    ) as client, pytest.raises(
        DemoLoadError,
        match="run run-1 failed with status failed",
    ):
        load_demo_source(
            client,
            _demo_source(source_path),
            sleep=lambda _: None,
        )


def test_default_demo_sources_reference_committed_public_fixtures() -> None:
    repository_root = Path(__file__).resolve().parents[2]

    sources = default_demo_sources(repository_root)

    assert [
        (
            source.path.name,
            source.provider,
            source.realm,
            source.account_id,
            source.export_type,
            source.region,
        )
        for source in sources
    ] == [
        (
            "aws-resource-explorer.csv",
            "aws",
            "commercial",
            "123456789012",
            "aws.resource_explorer.csv.v1",
            "ap-northeast-2",
        ),
        (
            "ncp-server-list.xlsx",
            "ncp",
            "government",
            "ncp-demo-government",
            "ncp.server_list.xlsx.v1",
            "KR",
        ),
        (
            "full-inventory.json",
            "aws",
            "commercial",
            "123456789012",
            "canonical.import_bundle.v1",
            None,
        ),
    ]
    assert all(source.path.is_file() for source in sources)
