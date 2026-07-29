# Local Workflow Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate plain local startup, deterministic demo loading, and full integration testing while documenting the manual upload path and updating GitHub About.

**Architecture:** Shared Shell functions own local prerequisites, secret generation, environment loading, and health checks. Thin executable scripts expose one responsibility each. A typed Python demo loader drives the existing Import, Preview, Apply, and Run APIs with fixed public fixtures so repeated demo loads reuse existing records.

**Tech Stack:** Bash, Docker Compose v2, Python 3.12, httpx, openpyxl, pytest, Ruff, mypy, GitHub CLI

## Global Constraints

- `scripts/start_local.sh` starts and provisions the local stack without loading demo or test data.
- `scripts/load_demo.sh` loads fixed synthetic data into an already running stack and never starts Docker Compose.
- `scripts/test_integration.sh` owns the full Compose and randomized E2E verification flow.
- Existing `.env` files are never overwritten and generated files use mode `0600`.
- Secrets, tokens, source file contents, and real cloud identifiers are never printed or committed.
- Demo fixtures use documentation-only identifiers and produce stable file hashes across runs.
- Repeated demo loads reuse the existing Import and Run instead of creating duplicates.
- `scripts/poc_import.sh` remains as a compatibility wrapper and is no longer the README default.
- The project does not add a task runner or a new runtime dependency.
- Korean prose does not use the middle dot character.

---

### Task 1: Shared Local Stack Startup

**Files:**
- Create: `scripts/lib/local_stack.sh`
- Create: `scripts/start_local.sh`
- Create: `tests/scripts/test_workflow_scripts.py`

**Interfaces:**
- Consumes: `compose.yaml`, `.env`, `scripts/apply_netbox_schema.py`
- Produces: `require_commands(name...) -> exit status`, `require_compose_v2() -> exit status`, `ensure_local_env() -> None`, `load_local_env() -> None`, `wait_for_url(service_name, url) -> exit status`, executable `scripts/start_local.sh`

- [ ] **Step 1: Write failing script behavior tests**

Create `tests/scripts/test_workflow_scripts.py`:

- Copy the real scripts into a temporary repository.
- Add a complete `.env` with synthetic values.
- Put fake `curl`, `docker`, `openssl`, and `uv` executables first in `PATH`.
- Have each fake executable append its received command and relevant environment variables to a command log.
- Execute `start_local.sh` as a subprocess.
- Assert the command log contains Compose version detection, `docker compose up -d --build`, both health checks, and `uv run python scripts/apply_netbox_schema.py`.
- Assert the log contains no pytest command and stdout contains the Import UI address.
- Execute `bash -n` against the shared library and startup script.

- [ ] **Step 2: Run the contract tests and confirm the missing scripts fail**

Run:

```bash
uv run pytest tests/scripts/test_workflow_scripts.py -q
```

Expected: FAIL because `scripts/lib/local_stack.sh` and `scripts/start_local.sh` do not exist.

- [ ] **Step 3: Extract shared local stack functions**

Create `scripts/lib/local_stack.sh` with:

```bash
#!/usr/bin/env bash

repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

require_commands() {
  local command_name
  for command_name in "$@"; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
      echo "required command is missing: $command_name" >&2
      return 1
    fi
  done
}

require_compose_v2() {
  if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose v2 is required" >&2
    return 1
  fi
}

ensure_local_env() {
  if [[ -e "$repository_root/.env" ]]; then
    return
  fi

  local temporary_env
  local control_postgres_password
  local netbox_postgres_password
  local netbox_secret_key
  local netbox_api_token_pepper
  local netbox_superuser_password
  local netbox_superuser_api_key
  local netbox_superuser_api_token
  local inventory_csrf_secret

  control_postgres_password="$(openssl rand -hex 32)"
  netbox_postgres_password="$(openssl rand -hex 32)"
  netbox_secret_key="$(openssl rand -hex 64)"
  netbox_api_token_pepper="$(openssl rand -hex 64)"
  netbox_superuser_password="$(openssl rand -hex 32)"
  netbox_superuser_api_key="$(openssl rand -hex 6)"
  netbox_superuser_api_token="$(openssl rand -hex 32)"
  inventory_csrf_secret="$(openssl rand -hex 32)"
  temporary_env="$(mktemp "${TMPDIR:-/tmp}/netbox-cloud-inventory-env.XXXXXX")"
  trap 'rm -f -- "${temporary_env:-}"' EXIT

  {
    printf 'CONTROL_POSTGRES_PASSWORD=%s\n' "$control_postgres_password"
    printf 'NETBOX_POSTGRES_PASSWORD=%s\n' "$netbox_postgres_password"
    printf 'NETBOX_SECRET_KEY=%s\n' "$netbox_secret_key"
    printf 'SECRET_KEY=%s\n' "$netbox_secret_key"
    printf 'NETBOX_API_TOKEN_PEPPER=%s\n' "$netbox_api_token_pepper"
    printf 'API_TOKEN_PEPPER_1=%s\n' "$netbox_api_token_pepper"
    printf 'NETBOX_SUPERUSER_PASSWORD=%s\n' "$netbox_superuser_password"
    printf 'SUPERUSER_PASSWORD=%s\n' "$netbox_superuser_password"
    printf 'NETBOX_SUPERUSER_API_KEY=%s\n' "$netbox_superuser_api_key"
    printf 'SUPERUSER_API_KEY=%s\n' "$netbox_superuser_api_key"
    printf 'NETBOX_SUPERUSER_API_TOKEN=%s\n' "$netbox_superuser_api_token"
    printf 'SUPERUSER_API_TOKEN=%s\n' "$netbox_superuser_api_token"
    printf 'INVENTORY_CSRF_SECRET=%s\n' "$inventory_csrf_secret"
    printf 'INVENTORY_DATABASE_URL=postgresql+psycopg://inventory:%s@control-postgres:5432/inventory\n' "$control_postgres_password"
    printf 'INVENTORY_NETBOX_URL=http://127.0.0.1:8000\n'
    printf 'INVENTORY_NETBOX_TOKEN=nbt_%s.%s\n' \
      "$netbox_superuser_api_key" \
      "$netbox_superuser_api_token"
  } >"$temporary_env"

  chmod 0600 "$temporary_env"
  mv "$temporary_env" "$repository_root/.env"
  trap - EXIT
  echo "created local .env with mode 0600"
}

load_local_env() {
  set -a
  # shellcheck disable=SC1091
  source "$repository_root/.env"
  set +a

  : "${CONTROL_POSTGRES_PASSWORD:?missing CONTROL_POSTGRES_PASSWORD in .env}"
  : "${NETBOX_POSTGRES_PASSWORD:?missing NETBOX_POSTGRES_PASSWORD in .env}"
  : "${SECRET_KEY:?missing SECRET_KEY in .env}"
  : "${API_TOKEN_PEPPER_1:?missing API_TOKEN_PEPPER_1 in .env}"
  : "${SUPERUSER_PASSWORD:?missing SUPERUSER_PASSWORD in .env}"
  : "${SUPERUSER_API_KEY:?missing SUPERUSER_API_KEY in .env}"
  : "${SUPERUSER_API_TOKEN:?missing SUPERUSER_API_TOKEN in .env}"
  : "${INVENTORY_CSRF_SECRET:?missing INVENTORY_CSRF_SECRET in .env}"
  : "${INVENTORY_NETBOX_TOKEN:?missing INVENTORY_NETBOX_TOKEN in .env}"
}

wait_for_url() {
  local service_name="$1"
  local url="$2"
  local attempt
  for attempt in $(seq 1 90); do
    if curl --fail --silent --show-error "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "$service_name did not become healthy: $url" >&2
  return 1
}
```

Keep the existing `.env` keys and token construction exactly compatible with `compose.yaml`.

- [ ] **Step 4: Implement the plain startup script**

Create `scripts/start_local.sh`:

```bash
#!/usr/bin/env bash

set -euo pipefail
umask 077

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/local_stack.sh
source "$script_directory/lib/local_stack.sh"
cd "$repository_root"

require_commands curl docker openssl uv
require_compose_v2
ensure_local_env
load_local_env

docker compose up -d --build
wait_for_url "NetBox" "http://127.0.0.1:8000/login/"
wait_for_url "Inventory API" "http://127.0.0.1:8080/healthz"
uv run python scripts/apply_netbox_schema.py

echo "local stack is ready"
echo "Import UI: http://127.0.0.1:8080/ui/imports"
echo "NetBox: http://127.0.0.1:8000"
echo "Health: http://127.0.0.1:8080/healthz"
```

Set executable mode on `scripts/start_local.sh`.

- [ ] **Step 5: Run the script contract tests**

Run:

```bash
uv run pytest tests/scripts/test_workflow_scripts.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the startup split**

```bash
git add scripts/lib/local_stack.sh scripts/start_local.sh tests/scripts/test_workflow_scripts.py
git commit -m "feat: separate plain local startup"
```

### Task 2: Deterministic Demo Loader

**Files:**
- Create: `src/cloud_inventory/demo_loader.py`
- Create: `scripts/load_demo_data.py`
- Create: `examples/demo/aws-resource-explorer.csv`
- Create: `examples/demo/ncp-server-list.xlsx`
- Create: `examples/demo/full-inventory.json`
- Create: `tests/unit/test_demo_loader.py`

**Interfaces:**
- Consumes: `POST /imports`, `GET /imports/{import_id}/preview`, `POST /imports/{import_id}/apply`, `GET /runs/{run_id}`
- Produces: `DemoSource`, `DemoResult`, `DemoLoadError`, `default_demo_sources(repository_root)`, `load_demo_source(client, source, sleep, attempts)`, CLI `scripts/load_demo_data.py`

- [ ] **Step 1: Write failing demo loader tests**

Create `tests/unit/test_demo_loader.py` with a stateful `httpx.MockTransport`:

```python
from pathlib import Path

import httpx
import pytest

from cloud_inventory.demo_loader import (
    DemoLoadError,
    DemoSource,
    load_demo_source,
)


def demo_source(path: Path) -> DemoSource:
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


def test_load_demo_source_uploads_previews_applies_and_waits_for_success(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "aws.csv"
    source_path.write_text(
        "Identifier,Resource type,Region,AWS account\n"
        "bucket,s3:bucket,,123456789012\n",
        encoding="utf-8",
    )
    calls: list[str] = []
    run_polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal run_polls
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "POST" and request.url.path == "/imports":
            return httpx.Response(
                202,
                json={"import_id": "import-1", "parse_job_id": "parse-1"},
            )
        if request.method == "GET" and request.url.path.endswith("/preview"):
            return httpx.Response(
                200,
                json={
                    "import_id": "import-1",
                    "batch_hash": "a" * 64,
                    "summary": {"create": 1, "error": 0},
                },
            )
        if request.method == "POST" and request.url.path.endswith("/apply"):
            return httpx.Response(202, json={"run_id": "run-1"})
        run_polls += 1
        status = "running" if run_polls == 1 else "succeeded"
        return httpx.Response(
            200,
            json={"id": "run-1", "status": status, "summary": {"create": 1}},
        )

    with httpx.Client(
        base_url="http://inventory.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = load_demo_source(
            client,
            demo_source(source_path),
            sleep=lambda _: None,
        )

    assert result.import_id == "import-1"
    assert result.run_id == "run-1"
    assert result.summary == {"create": 1}
    assert calls == [
        "POST /imports",
        "GET /imports/import-1/preview",
        "POST /imports/import-1/apply",
        "GET /runs/run-1",
        "GET /runs/run-1",
    ]


def test_load_demo_source_retries_preview_not_ready(tmp_path: Path) -> None:
    source_path = tmp_path / "aws.csv"
    source_path.write_text(
        "Identifier,Resource type,Region,AWS account\n",
        encoding="utf-8",
    )
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
            return httpx.Response(
                200,
                json={
                    "import_id": "import-1",
                    "batch_hash": "a" * 64,
                    "summary": {"error": 0},
                },
            )
        if request.url.path.endswith("/apply"):
            return httpx.Response(200, json={"run_id": "run-1"})
        return httpx.Response(
            200,
            json={"id": "run-1", "status": "succeeded", "summary": {}},
        )

    with httpx.Client(
        base_url="http://inventory.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        load_demo_source(
            client,
            demo_source(source_path),
            sleep=lambda _: None,
        )

    assert preview_polls == 2


def test_load_demo_source_rejects_preview_errors(tmp_path: Path) -> None:
    source_path = tmp_path / "aws.csv"
    source_path.write_text(
        "Identifier,Resource type,Region,AWS account\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/imports":
            return httpx.Response(
                202,
                json={"import_id": "import-1", "parse_job_id": "parse-1"},
            )
        return httpx.Response(
            200,
            json={
                "import_id": "import-1",
                "batch_hash": "a" * 64,
                "summary": {"error": 1},
            },
        )

    with httpx.Client(
        base_url="http://inventory.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(DemoLoadError, match="preview contains 1 error"):
            load_demo_source(
                client,
                demo_source(source_path),
                sleep=lambda _: None,
            )


def test_load_demo_source_rejects_failed_run(tmp_path: Path) -> None:
    source_path = tmp_path / "aws.csv"
    source_path.write_text(
        "Identifier,Resource type,Region,AWS account\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/imports":
            return httpx.Response(
                202,
                json={"import_id": "import-1", "parse_job_id": "parse-1"},
            )
        if request.url.path.endswith("/preview"):
            return httpx.Response(
                200,
                json={
                    "import_id": "import-1",
                    "batch_hash": "a" * 64,
                    "summary": {"error": 0},
                },
            )
        if request.url.path.endswith("/apply"):
            return httpx.Response(202, json={"run_id": "run-1"})
        return httpx.Response(
            200,
            json={
                "id": "run-1",
                "status": "failed",
                "summary": {"error": "NetBox rejected the write"},
            },
        )

    with httpx.Client(
        base_url="http://inventory.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(DemoLoadError, match="run run-1 failed"):
            load_demo_source(
                client,
                demo_source(source_path),
                sleep=lambda _: None,
            )
```

- [ ] **Step 2: Run the loader tests and confirm the missing module fails**

Run:

```bash
uv run pytest tests/unit/test_demo_loader.py -q
```

Expected: collection ERROR with `ModuleNotFoundError: cloud_inventory.demo_loader`.

- [ ] **Step 3: Implement the typed demo loader**

Create `src/cloud_inventory/demo_loader.py` with these public types and behavior:

```python
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class DemoLoadError(RuntimeError):
    pass


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
```

Add response validation and polling helpers:

```python
JsonObject = dict[str, Any]
NON_TERMINAL_RUN_STATES = frozenset(
    {"queued", "running", "retry_scheduled"}
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
```

Implement:

```python
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
    error_count = raw_error_count
    if error_count:
        raise DemoLoadError(
            f"{source.name}: preview contains {error_count} error(s)"
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
```

- [ ] **Step 4: Add fixed public demo fixtures**

Create:

- `examples/demo/aws-resource-explorer.csv` from the existing public AWS fixture
- `examples/demo/full-inventory.json` from the existing canonical public fixture
- `examples/demo/ncp-server-list.xlsx` with one synthetic NCP government server

The XLSX workbook must contain this exact header and row:

```text
Server Name | Instance ID | Status | Region | Zone | VPC | Subnet | Private IP
demo-ncp-web-01 | demo-server-001 | RUN | KR | KR-1 | demo-vpc | demo-subnet | 192.0.2.20
```

Generate the workbook once with openpyxl and commit the resulting binary. Verify that a second `sha256sum examples/demo/ncp-server-list.xlsx` call returns the same value because the loader reads the committed file rather than regenerating it.

- [ ] **Step 5: Add the CLI entrypoint**

Create `scripts/load_demo_data.py`:

```python
import os
from pathlib import Path

import httpx

from cloud_inventory.demo_loader import (
    DemoLoadError,
    default_demo_sources,
    load_demo_sources,
)


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    api_url = os.getenv(
        "INVENTORY_API_URL",
        "http://127.0.0.1:8080",
    ).rstrip("/")
    try:
        with httpx.Client(base_url=api_url, timeout=30) as client:
            results = load_demo_sources(
                client,
                default_demo_sources(repository_root),
            )
    except DemoLoadError as error:
        print(f"demo import failed: {error}")
        return 1

    for result in results:
        print(
            f"{result.name}: import={result.import_id} "
            f"run={result.run_id} summary={result.summary}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Use stderr for failure output in the final implementation.

- [ ] **Step 6: Run focused loader tests**

Run:

```bash
uv run pytest tests/unit/test_demo_loader.py -q
uv run ruff check src/cloud_inventory/demo_loader.py scripts/load_demo_data.py tests/unit/test_demo_loader.py
uv run mypy src
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit the deterministic demo loader**

```bash
git add src/cloud_inventory/demo_loader.py scripts/load_demo_data.py examples/demo tests/unit/test_demo_loader.py
git commit -m "feat: add deterministic demo loader"
```

### Task 3: Demo and Integration Script Entrypoints

**Files:**
- Modify: `scripts/load_demo.sh`
- Modify: `scripts/test_integration.sh`
- Modify: `scripts/poc_import.sh`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/scripts/test_workflow_scripts.py`

**Interfaces:**
- Consumes: `scripts/lib/local_stack.sh`, `scripts/start_local.sh`, `scripts/load_demo_data.py`, `tests/integration/test_manual_import_flow.py`
- Produces: user command `./scripts/load_demo.sh`, CI command `./scripts/test_integration.sh`, compatible command `./scripts/poc_import.sh`

- [ ] **Step 1: Add failing role-boundary behavior tests**

Extend `tests/scripts/test_workflow_scripts.py`:

- Execute `load_demo.sh` with healthy fake endpoints and assert it runs the Python demo loader without invoking Docker.
- Execute `load_demo.sh` with a failing fake endpoint and assert it exits nonzero, recommends `start_local.sh`, and never invokes uv.
- Execute `test_integration.sh` and assert schema application occurs before pytest with `RUN_MANUAL_IMPORT_E2E=1` and the local Inventory API URL.
- Execute `poc_import.sh` against a fake `test_integration.sh` and assert argument forwarding and the deprecation message.
- Execute `bash -n` against all five Shell files.

- [ ] **Step 2: Run the focused tests and confirm the missing entrypoints fail**

Run:

```bash
uv run pytest tests/scripts/test_workflow_scripts.py -q
```

Expected: FAIL because `scripts/load_demo.sh` and `scripts/test_integration.sh` do not exist and the legacy script still contains the combined workflow.

- [ ] **Step 3: Implement the demo loading script**

Replace `scripts/load_demo.sh` with:

```bash
#!/usr/bin/env bash

set -euo pipefail
umask 077

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/local_stack.sh
source "$script_directory/lib/local_stack.sh"
cd "$repository_root"

require_commands curl uv
if [[ ! -e .env ]]; then
  echo "local environment is not initialized; run ./scripts/start_local.sh first" >&2
  exit 1
fi
load_local_env

if ! wait_for_url "Inventory API" "http://127.0.0.1:8080/healthz"; then
  echo "run ./scripts/start_local.sh before loading demo data" >&2
  exit 1
fi
wait_for_url "NetBox" "http://127.0.0.1:8000/login/"

INVENTORY_API_URL=http://127.0.0.1:8080 \
  uv run python scripts/load_demo_data.py

echo "demo data loaded"
echo "Import UI: http://127.0.0.1:8080/ui/imports"
echo "NetBox: http://127.0.0.1:8000"
```

- [ ] **Step 4: Implement the integration script and compatibility wrapper**

Replace `scripts/test_integration.sh` with:

```bash
#!/usr/bin/env bash

set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"$script_directory/start_local.sh"

cd "$(cd -- "$script_directory/.." && pwd)"
set -a
# shellcheck disable=SC1091
source .env
set +a

RUN_MANUAL_IMPORT_E2E=1 \
  INVENTORY_API_URL=http://127.0.0.1:8080 \
  uv run pytest tests/integration/test_manual_import_flow.py -q
```

Replace `scripts/poc_import.sh` with:

```bash
#!/usr/bin/env bash

set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
echo "scripts/poc_import.sh is deprecated; use scripts/test_integration.sh" >&2
exec "$script_directory/test_integration.sh" "$@"
```

Ensure all three files are executable.

- [ ] **Step 5: Update the manual Compose integration workflow**

Change `.github/workflows/ci.yml`:

```yaml
      - name: Run the full local integration test
        run: ./scripts/test_integration.sh
```

- [ ] **Step 6: Run focused script checks**

Run:

```bash
uv run pytest tests/scripts/test_workflow_scripts.py -q
bash -n scripts/lib/local_stack.sh scripts/start_local.sh scripts/load_demo.sh scripts/test_integration.sh scripts/poc_import.sh
docker compose config --quiet
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit the entrypoint split**

```bash
git add scripts/load_demo.sh scripts/test_integration.sh scripts/poc_import.sh .github/workflows/ci.yml tests/scripts/test_workflow_scripts.py
git commit -m "build: split demo and integration workflows"
```

### Task 4: README and Manual Import Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/manual-import.md`
- Modify: `docs/superpowers/specs/2026-07-29-local-workflow-split-design.md`

**Interfaces:**
- Consumes: the final commands from Tasks 1 through 3
- Produces: separate plain startup, demo loading, actual Export upload, integration test, shutdown, and reset instructions

- [ ] **Step 1: Rewrite the README quick start**

Replace the single `poc_import.sh` flow with these sections:

1. Prerequisites and `uv sync --locked --all-groups`
2. Plain local startup:

```bash
./scripts/start_local.sh
```

Explain that this starts Compose, waits for services, applies the NetBox schema, and loads no demo data.

3. Actual Export upload:
   - Open `http://127.0.0.1:8080/ui/imports`
   - Enter Provider, Realm, Account ID, Export type, Exported at, and optional Region
   - Upload CSV, XLSX, or JSON
   - Review Preview
   - Apply
   - Inspect NetBox

4. Demo loading:

```bash
./scripts/load_demo.sh
```

Explain the fixed public fixtures and repeated-load idempotency.

5. Full integration verification:

```bash
./scripts/test_integration.sh
```

Explain that this creates randomized synthetic test records and is primarily for development and CI.

6. Shutdown:

```bash
docker compose down
```

7. Destructive reset:

```bash
docker compose down -v
```

Place a warning immediately above the reset command that it deletes NetBox data, Import history, and stored Artifacts.

- [ ] **Step 2: Update the manual Import guide**

At the beginning of `docs/manual-import.md`, add:

```bash
./scripts/start_local.sh
```

State that `load_demo.sh` is optional and must not be run when the operator wants an empty environment for their own Export. Link the upload steps back to the README service addresses.

Correct the design document wording so it records that CSV, XLSX, and JSON are fixed committed fixtures rather than dynamically generated data.

- [ ] **Step 3: Run the documentation and diff checks**

Run:

```bash
uv run pytest tests/scripts/test_workflow_scripts.py -q
rg -n $'\u00b7|poc_import\\.sh.*가장|가장.*poc_import\\.sh' README.md docs scripts
git diff --check
```

Expected: pytest passes, `rg` prints no forbidden middle dot or obsolete default-flow match, and `git diff --check` exits 0. Human-readable documentation does not add a source-text assertion test.

- [ ] **Step 4: Commit the documentation**

```bash
git add README.md docs/manual-import.md docs/superpowers/specs/2026-07-29-local-workflow-split-design.md tests/scripts/test_workflow_scripts.py
git commit -m "docs: explain local import workflows"
```

### Task 5: Full Verification and GitHub About

**Files:**
- Verify: all changed source, scripts, examples, tests, docs, and workflow files
- External update: `KimHG1995/netbox-cloud-inventory` GitHub repository About

**Interfaces:**
- Consumes: all prior task outputs and authenticated GitHub CLI access
- Produces: verified local workflows and updated repository description and Topics

- [ ] **Step 1: Run the complete non-Docker quality suite**

Run:

```bash
uv run ruff check src tests scripts
uv run mypy src
uv run pytest tests/unit tests/api tests/scripts -q
uv run python scripts/export_import_schema.py --check
docker compose config --quiet
bash -n scripts/lib/local_stack.sh scripts/start_local.sh scripts/load_demo.sh scripts/test_integration.sh scripts/poc_import.sh
```

Expected: every command exits 0.

- [ ] **Step 2: Run the plain startup**

Run:

```bash
./scripts/start_local.sh
```

Expected: Compose services become healthy, NetBox schema application succeeds, and no demo loader or pytest output appears.

- [ ] **Step 3: Load the demo twice and verify idempotency**

Run:

```bash
./scripts/load_demo.sh
./scripts/load_demo.sh
```

Expected: both commands exit 0 and each named demo source reports the same Import ID and Run ID on the second invocation.

- [ ] **Step 4: Run the randomized full integration test**

Run:

```bash
./scripts/test_integration.sh
```

Expected: `tests/integration/test_manual_import_flow.py` passes.

- [ ] **Step 5: Inspect current GitHub About before changing it**

Run:

```bash
gh repo view KimHG1995/netbox-cloud-inventory \
  --json description,homepageUrl,repositoryTopics
```

Record the current description, homepage, and Topics. Do not set a homepage because there is no public deployment.

- [ ] **Step 6: Update GitHub About**

Run:

```bash
gh repo edit KimHG1995/netbox-cloud-inventory \
  --description "Manual AWS and NAVER Cloud inventory imports normalized into NetBox for centralized infrastructure discovery." \
  --add-topic netbox,aws,naver-cloud-platform,cloud-inventory,infrastructure-inventory,fastapi,python,docker
```

- [ ] **Step 7: Verify GitHub About**

Run:

```bash
gh repo view KimHG1995/netbox-cloud-inventory \
  --json description,homepageUrl,repositoryTopics
```

Expected: the exact description is present, all eight Topics are present, and `homepageUrl` remains empty.

- [ ] **Step 8: Review final repository state**

Run:

```bash
git status -sb
git log --oneline origin/main..main
git diff origin/main...main --stat
```

Report the local commits separately from the confirmed GitHub About update. Do not push code unless the user separately authorizes pushing the new commits.
