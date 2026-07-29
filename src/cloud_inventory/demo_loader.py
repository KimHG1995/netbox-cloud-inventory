import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

JsonObject = dict[str, Any]
NON_TERMINAL_RUN_STATES = frozenset(
    {"queued", "running", "retry_scheduled"}
)


class DemoLoadError(RuntimeError):
    """Raised when demo data cannot be safely previewed and applied."""


@dataclass(frozen=True)
class DemoSource:
    name: str
    path: Path
    media_type: str
    provider: str
    realm: str
    account_id: str
    export_type: str
    exported_at: str
    region: str | None


@dataclass(frozen=True)
class DemoResult:
    name: str
    import_id: str
    run_id: str
    summary: dict[str, Any]


def default_demo_sources(repository_root: Path) -> tuple[DemoSource, ...]:
    root = repository_root / "examples" / "demo"
    return (
        DemoSource(
            name="AWS Resource Explorer",
            path=root / "aws-resource-explorer.csv",
            media_type="text/csv",
            provider="aws",
            realm="commercial",
            account_id="123456789012",
            export_type="aws.resource_explorer.csv.v1",
            exported_at="2026-07-28T00:00:00+00:00",
            region="ap-northeast-2",
        ),
        DemoSource(
            name="NCP government Server",
            path=root / "ncp-server-list.xlsx",
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            provider="ncp",
            realm="government",
            account_id="ncp-demo-government",
            export_type="ncp.server_list.xlsx.v1",
            exported_at="2026-07-28T00:00:00+00:00",
            region="KR",
        ),
        DemoSource(
            name="Canonical full inventory",
            path=root / "full-inventory.json",
            media_type="application/json",
            provider="aws",
            realm="commercial",
            account_id="123456789012",
            export_type="canonical.import_bundle.v1",
            exported_at="2026-07-28T00:00:00+00:00",
            region=None,
        ),
    )


def _safe_detail(response: httpx.Response) -> str:
    try:
        document = response.json()
    except ValueError:
        return "non-JSON error response"
    if not isinstance(document, dict):
        return "unexpected error response"
    detail = document.get("detail")
    if isinstance(detail, dict):
        code = detail.get("code")
        message = detail.get("message")
        return f"{code}: {message}"
    if isinstance(detail, str):
        return detail
    return "unexpected error response"


def _json_object(
    response: httpx.Response,
    context: str,
) -> JsonObject:
    if response.is_error:
        raise DemoLoadError(
            f"{context}: HTTP {response.status_code} "
            f"{_safe_detail(response)}"
        )
    try:
        document = response.json()
    except ValueError as error:
        raise DemoLoadError(f"{context}: invalid JSON response") from error
    if not isinstance(document, dict):
        raise DemoLoadError(f"{context}: expected a JSON object")
    return document


def _required_string(
    document: JsonObject,
    key: str,
    context: str,
) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise DemoLoadError(f"{context}: missing {key}")
    return value


def _preview_not_ready(response: httpx.Response) -> bool:
    if response.status_code != 409:
        return False
    try:
        document = response.json()
    except ValueError:
        return False
    if not isinstance(document, dict):
        return False
    detail = document.get("detail")
    return (
        isinstance(detail, dict)
        and detail.get("code") == "preview_not_ready"
    )


def _wait_for_preview(
    client: httpx.Client,
    import_id: str,
    *,
    sleep: Callable[[float], None],
    attempts: int,
) -> JsonObject:
    for _ in range(attempts):
        try:
            response = client.get(f"/imports/{import_id}/preview")
        except httpx.HTTPError as error:
            raise DemoLoadError(
                f"preview {import_id}: request failed"
            ) from error
        if _preview_not_ready(response):
            sleep(0.25)
            continue
        return _json_object(response, f"preview {import_id}")
    raise DemoLoadError(f"preview {import_id}: timed out")


def _wait_for_run(
    client: httpx.Client,
    run_id: str,
    *,
    sleep: Callable[[float], None],
    attempts: int,
) -> JsonObject:
    for _ in range(attempts):
        try:
            response = client.get(f"/runs/{run_id}")
        except httpx.HTTPError as error:
            raise DemoLoadError(f"run {run_id}: request failed") from error
        run = _json_object(response, f"run {run_id}")
        status = _required_string(run, "status", f"run {run_id}")
        if status == "succeeded":
            return run
        if status not in NON_TERMINAL_RUN_STATES:
            raise DemoLoadError(f"run {run_id} failed with status {status}")
        sleep(0.25)
    raise DemoLoadError(f"run {run_id}: timed out")


def load_demo_source(
    client: httpx.Client,
    source: DemoSource,
    *,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 120,
) -> DemoResult:
    try:
        with source.path.open("rb") as stream:
            upload_response = client.post(
                "/imports",
                data={
                    "provider": source.provider,
                    "realm": source.realm,
                    "account_id": source.account_id,
                    "export_type": source.export_type,
                    "exported_at": source.exported_at,
                    **({"region": source.region} if source.region else {}),
                },
                files={
                    "files": (
                        source.path.name,
                        stream,
                        source.media_type,
                    )
                },
            )
    except OSError as error:
        raise DemoLoadError(
            f"{source.name}: cannot read {source.path.name}"
        ) from error
    except httpx.HTTPError as error:
        raise DemoLoadError(f"{source.name}: upload request failed") from error

    upload = _json_object(upload_response, f"{source.name} upload")
    import_id = _required_string(upload, "import_id", source.name)
    preview = _wait_for_preview(
        client,
        import_id,
        sleep=sleep,
        attempts=attempts,
    )
    preview_summary = preview.get("summary")
    if not isinstance(preview_summary, dict):
        raise DemoLoadError(f"{source.name}: invalid preview summary")
    raw_error_count = preview_summary.get("error", 0)
    if not isinstance(raw_error_count, int):
        raise DemoLoadError(f"{source.name}: invalid preview error count")
    if raw_error_count:
        raise DemoLoadError(
            f"{source.name}: preview contains {raw_error_count} error(s)"
        )
    batch_hash = _required_string(
        preview,
        "batch_hash",
        f"{source.name} preview",
    )
    try:
        apply_response = client.post(
            f"/imports/{import_id}/apply",
            json={
                "batch_hash": batch_hash,
                "apply_valid_only": False,
            },
        )
    except httpx.HTTPError as error:
        raise DemoLoadError(f"{source.name}: apply request failed") from error
    apply_document = _json_object(
        apply_response,
        f"{source.name} apply",
    )
    run_id = _required_string(
        apply_document,
        "run_id",
        f"{source.name} apply",
    )
    run = _wait_for_run(
        client,
        run_id,
        sleep=sleep,
        attempts=attempts,
    )
    raw_summary = run.get("summary", {})
    if not isinstance(raw_summary, dict):
        raise DemoLoadError(f"run {run_id}: invalid summary")
    return DemoResult(
        name=source.name,
        import_id=import_id,
        run_id=run_id,
        summary=dict(raw_summary),
    )


def load_demo_sources(
    client: httpx.Client,
    sources: Sequence[DemoSource],
) -> list[DemoResult]:
    return [load_demo_source(client, source) for source in sources]
