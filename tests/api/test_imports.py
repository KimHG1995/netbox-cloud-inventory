import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl, SecretStr

from cloud_inventory.api.dependencies import (
    get_artifact_store,
    get_import_repository,
    get_job_queue,
)
from cloud_inventory.api.imports import (
    ImportRecord,
    PreviewPage,
    RunRecord,
    SourceFileRecord,
)
from cloud_inventory.app import create_app
from cloud_inventory.config import Settings, get_settings
from cloud_inventory.ingest.artifact_store import InMemoryArtifactStore

NOW = datetime(2026, 7, 28, 3, 0, tzinfo=UTC)
AWS_CSV = (
    b"Arn,Region,ResourceType,Service,Tags\n"
    b"arn:aws:ec2:ap-northeast-2:111111111111:instance/i-123,"
    b"ap-northeast-2,ec2:instance,ec2,{}\n"
)


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: dict[str, UUID] = {}
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> UUID:
        self.calls.append((job_type, payload, idempotency_key))
        return self.jobs.setdefault(idempotency_key, uuid4())


class FakeRepository:
    def __init__(self) -> None:
        self.imports: dict[UUID, ImportRecord] = {}
        self.by_fingerprint: dict[str, UUID] = {}
        self.sources: dict[str, SourceFileRecord] = {}
        self.previews: dict[UUID, PreviewPage] = {}
        self.runs: dict[UUID, RunRecord] = {}
        self.runs_by_key: dict[str, UUID] = {}

    async def find_import_by_fingerprint(
        self,
        fingerprint: str,
    ) -> ImportRecord | None:
        import_id = self.by_fingerprint.get(fingerprint)
        return self.imports.get(import_id) if import_id is not None else None

    async def find_source_conflicts(
        self,
        deduplication_keys: list[str],
    ) -> list[SourceFileRecord]:
        return [
            self.sources[key]
            for key in deduplication_keys
            if key in self.sources
        ]

    async def create_import(
        self,
        record: ImportRecord,
        source_files: list[SourceFileRecord],
    ) -> None:
        self.imports[record.id] = record
        self.by_fingerprint[record.request_fingerprint] = record.id
        for source in source_files:
            self.sources[source.deduplication_key] = source

    async def delete_import(self, import_id: UUID) -> None:
        record = self.imports.pop(import_id, None)
        if record is not None:
            self.by_fingerprint.pop(record.request_fingerprint, None)
        self.sources = {
            key: source
            for key, source in self.sources.items()
            if source.import_id != import_id
        }

    async def get_preview_page(
        self,
        import_id: UUID,
        offset: int,
        limit: int,
    ) -> PreviewPage | None:
        preview = self.previews.get(import_id)
        if preview is None:
            return None
        return preview.model_copy(
            update={
                "offset": offset,
                "limit": limit,
                "changes": preview.changes[offset : offset + limit],
            }
        )

    async def create_or_get_run(
        self,
        *,
        import_id: UUID,
        batch_hash: str,
        apply_valid_only: bool,
        idempotency_key: str,
    ) -> tuple[RunRecord, bool]:
        existing_id = self.runs_by_key.get(idempotency_key)
        if existing_id is not None:
            return self.runs[existing_id], False
        run = RunRecord(
            id=uuid4(),
            import_id=import_id,
            batch_hash=batch_hash,
            apply_valid_only=apply_valid_only,
            status="queued",
            checkpoint=None,
            summary={},
            started_at=None,
            finished_at=None,
        )
        self.runs[run.id] = run
        self.runs_by_key[idempotency_key] = run.id
        return run, True

    async def delete_run(self, run_id: UUID) -> None:
        self.runs.pop(run_id, None)

    async def get_run(self, run_id: UUID) -> RunRecord | None:
        return self.runs.get(run_id)


@pytest.fixture
def api() -> tuple[TestClient, FakeRepository, FakeQueue]:
    repository = FakeRepository()
    queue = FakeQueue()
    store = InMemoryArtifactStore()
    settings = Settings(
        database_url="postgresql+psycopg://inventory:test@localhost/inventory",
        artifact_root=Path("/tmp/test-inventory"),
        netbox_url=AnyHttpUrl("http://localhost:8000"),
        netbox_token=SecretStr("test-token"),
        csrf_secret=SecretStr("test-csrf"),
        max_file_bytes=1024 * 1024,
        max_files_per_import=20,
    )
    app = create_app()
    app.dependency_overrides[get_import_repository] = lambda: repository
    app.dependency_overrides[get_job_queue] = lambda: queue
    app.dependency_overrides[get_artifact_store] = lambda: store
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, repository, queue


def form_data(**overrides: str) -> dict[str, str]:
    values = {
        "provider": "aws",
        "realm": "commercial",
        "account_id": "111111111111",
        "export_type": "auto",
        "exported_at": NOW.isoformat(),
        "region": "ap-northeast-2",
    }
    values.update(overrides)
    return values


def test_upload_returns_parse_job_and_duplicate_returns_original(
    api: tuple[TestClient, FakeRepository, FakeQueue],
) -> None:
    client, _, queue = api
    files = {"files": ("resources.csv", AWS_CSV, "text/csv")}

    first = client.post("/imports", data=form_data(), files=files)
    second = client.post("/imports", data=form_data(), files=files)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(queue.calls) == 1
    assert queue.calls[0][0] == "parse_import"


def test_upload_rejects_existing_and_new_file_mix(
    api: tuple[TestClient, FakeRepository, FakeQueue],
) -> None:
    client, _, _ = api
    first_file = ("resources.csv", AWS_CSV, "text/csv")
    assert client.post(
        "/imports",
        data=form_data(),
        files={"files": first_file},
    ).status_code == 202

    response = client.post(
        "/imports",
        data=form_data(),
        files=[
            ("files", first_file),
            (
                "files",
                (
                    "other.csv",
                    AWS_CSV.replace(b"i-123", b"i-456"),
                    "text/csv",
                ),
            ),
        ],
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "duplicate_file_in_different_import"


def test_upload_enforces_future_time_and_content_type(
    api: tuple[TestClient, FakeRepository, FakeQueue],
) -> None:
    client, _, _ = api
    future = client.post(
        "/imports",
        data=form_data(
            exported_at=(datetime.now(UTC) + timedelta(minutes=6)).isoformat()
        ),
        files={"files": ("resources.csv", AWS_CSV, "text/csv")},
    )
    mismatch = client.post(
        "/imports",
        data=form_data(exported_at=datetime.now(UTC).isoformat()),
        files={"files": ("resources.json", AWS_CSV, "application/json")},
    )

    assert future.status_code == 422
    assert mismatch.status_code == 415


def test_upload_enforces_file_count_and_size(
    api: tuple[TestClient, FakeRepository, FakeQueue],
) -> None:
    client, _, _ = api
    too_many = [
        ("files", (f"{index}.csv", AWS_CSV, "text/csv"))
        for index in range(21)
    ]
    response = client.post("/imports", data=form_data(), files=too_many)

    assert response.status_code == 422

    app = client.app
    small_settings = Settings(
        database_url="postgresql+psycopg://inventory:test@localhost/inventory",
        netbox_token=SecretStr("test-token"),
        csrf_secret=SecretStr("test-csrf"),
        max_file_bytes=10,
    )
    app.dependency_overrides[get_settings] = lambda: small_settings
    oversized = client.post(
        "/imports",
        data=form_data(exported_at=datetime.now(UTC).isoformat()),
        files={"files": ("resources.csv", AWS_CSV, "text/csv")},
    )

    assert oversized.status_code == 413


def test_preview_and_apply_are_hash_bound_and_idempotent(
    api: tuple[TestClient, FakeRepository, FakeQueue],
) -> None:
    client, repository, queue = api
    import_id = uuid4()
    repository.previews[import_id] = PreviewPage(
        import_id=import_id,
        batch_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        summary={"create": 1, "update": 0, "unchanged": 0, "warning": 0, "error": 0},
        total_changes=1,
        offset=0,
        limit=100,
        changes=[
            {
                "cloud_uid": "uid-1",
                "resource_type": "virtual_machine",
                "action": "create",
                "changed_fields": ["name"],
                "warning_codes": [],
                "desired": {},
            }
        ],
    )

    preview = client.get(f"/imports/{import_id}/preview")
    stale = client.post(
        f"/imports/{import_id}/apply",
        json={"batch_hash": "b" * 64, "apply_valid_only": False},
    )
    first = client.post(
        f"/imports/{import_id}/apply",
        json={"batch_hash": "a" * 64, "apply_valid_only": False},
    )
    second = client.post(
        f"/imports/{import_id}/apply",
        json={"batch_hash": "a" * 64, "apply_valid_only": False},
    )

    assert preview.status_code == 200
    assert stale.status_code == 409
    assert first.status_code == 202
    assert second.status_code == 200
    assert first.json()["run_id"] == second.json()["run_id"]
    assert [call[0] for call in queue.calls] == ["apply_import"]


def test_preview_not_ready_expired_and_errors_block_apply(
    api: tuple[TestClient, FakeRepository, FakeQueue],
) -> None:
    client, repository, _ = api
    missing_id = uuid4()
    assert client.get(f"/imports/{missing_id}/preview").status_code == 409

    expired_id = uuid4()
    repository.previews[expired_id] = PreviewPage(
        import_id=expired_id,
        batch_hash="c" * 64,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        summary={"create": 0, "update": 0, "unchanged": 0, "warning": 0, "error": 1},
        total_changes=0,
        offset=0,
        limit=100,
        changes=[],
    )
    assert client.get(f"/imports/{expired_id}/preview").status_code == 410

    ready_id = uuid4()
    repository.previews[ready_id] = repository.previews[expired_id].model_copy(
        update={
            "import_id": ready_id,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        }
    )
    blocked = client.post(
        f"/imports/{ready_id}/apply",
        json={"batch_hash": "c" * 64, "apply_valid_only": False},
    )
    allowed = client.post(
        f"/imports/{ready_id}/apply",
        json={"batch_hash": "c" * 64, "apply_valid_only": True},
    )

    assert blocked.status_code == 409
    assert allowed.status_code == 202


def test_run_status_is_queryable(
    api: tuple[TestClient, FakeRepository, FakeQueue],
) -> None:
    client, repository, _ = api
    run = RunRecord(
        id=uuid4(),
        import_id=uuid4(),
        batch_hash=hashlib.sha256(b"batch").hexdigest(),
        apply_valid_only=False,
        status="running",
        checkpoint="3",
        summary={"create": 2},
        started_at=NOW,
        finished_at=None,
    )
    repository.runs[run.id] = run

    response = client.get(f"/runs/{run.id}")

    assert response.status_code == 200
    assert response.json()["checkpoint"] == "3"
