import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from cloud_inventory.api.dependencies import get_import_repository, get_job_queue
from cloud_inventory.api.imports import PreviewPage, SourceFileRecord
from cloud_inventory.api.ui import CsrfManager
from cloud_inventory.app import create_app
from cloud_inventory.config import Settings, get_settings


class FakeUiRepository:
    def __init__(self) -> None:
        self.preview: PreviewPage | None = None
        self.sources: list[SourceFileRecord] = []
        self.run_id = uuid4()

    async def get_preview_page(self, import_id, offset, limit):
        return self.preview

    async def get_source_files(self, import_id):
        return self.sources

    async def create_or_get_run(self, **kwargs):
        from cloud_inventory.api.imports import RunRecord

        return (
            RunRecord(
                id=self.run_id,
                import_id=kwargs["import_id"],
                batch_hash=kwargs["batch_hash"],
                apply_valid_only=kwargs["apply_valid_only"],
                status="queued",
                checkpoint=None,
                summary={},
                started_at=None,
                finished_at=None,
            ),
            True,
        )

    async def delete_run(self, run_id):
        return None


class FakeQueue:
    async def enqueue(self, job_type: str, payload: dict[str, Any], idempotency_key: str):
        return uuid4()


@pytest.fixture
def ui() -> tuple[TestClient, FakeUiRepository, Settings]:
    repository = FakeUiRepository()
    settings = Settings(
        database_url="postgresql+psycopg://inventory:test@localhost/inventory",
        artifact_root=Path("/tmp/test-ui"),
        netbox_token=SecretStr("test-token"),
        csrf_secret=SecretStr("test-csrf"),
    )
    app = create_app()
    app.dependency_overrides[get_import_repository] = lambda: repository
    app.dependency_overrides[get_job_queue] = lambda: FakeQueue()
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, repository, settings


def hidden_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def test_upload_form_has_required_fields_and_public_poc_warning(
    ui: tuple[TestClient, FakeUiRepository, Settings],
) -> None:
    client, _, _ = ui

    response = client.get("/ui/imports")

    assert response.status_code == 200
    for name in (
        "provider",
        "realm",
        "account_id",
        "export_type",
        "exported_at",
        "files",
    ):
        assert f'name="{name}"' in response.text
    assert 'name="files"' in response.text
    assert "multiple" in response.text
    assert "synthetic or approved files" in response.text
    assert response.cookies["cloud_inventory_csrf"] == hidden_token(response.text)


def test_preview_escapes_content_and_contains_hash_bound_apply_form(
    ui: tuple[TestClient, FakeUiRepository, Settings],
) -> None:
    client, repository, _ = ui
    import_id = uuid4()
    repository.preview = PreviewPage(
        import_id=import_id,
        batch_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        summary={"create": 1, "update": 0, "unchanged": 0, "warning": 1, "error": 0},
        total_changes=1,
        offset=0,
        limit=100,
        changes=[
            {
                "cloud_uid": "uid",
                "resource_type": "virtual_machine",
                "action": "create",
                "changed_fields": ["name"],
                "warning_codes": ["<script>alert(1)</script>"],
                "desired": {"name": "<b>server</b>"},
            }
        ],
    )
    repository.sources = [
        SourceFileRecord(
            id=uuid4(),
            import_id=import_id,
            filename="<img src=x onerror=alert(1)>.csv",
            media_type="text/csv",
            sha256="b" * 64,
            deduplication_key="c" * 64,
            size_bytes=1,
            artifact_key=f"imports/{import_id}/{uuid4()}/{'b' * 64}",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    ]

    response = client.get(f"/ui/imports/{import_id}")

    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "<img " not in response.text
    assert "&lt;b&gt;server&lt;/b&gt;" in response.text
    assert f'name="batch_hash" value="{"a" * 64}"' in response.text
    assert "warning" in response.text


def test_apply_rejects_missing_mismatched_invalid_and_expired_csrf(
    ui: tuple[TestClient, FakeUiRepository, Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repository, settings = ui
    import_id = uuid4()
    repository.preview = PreviewPage(
        import_id=import_id,
        batch_hash="d" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        summary={"create": 1, "update": 0, "unchanged": 0, "warning": 0, "error": 0},
        total_changes=0,
        offset=0,
        limit=100,
        changes=[],
    )
    url = f"/ui/imports/{import_id}/apply"
    body = {"batch_hash": "d" * 64, "apply_valid_only": "false"}

    client.cookies.clear()
    assert client.post(url, data=body).status_code == 403

    page = client.get(f"/ui/imports/{import_id}")
    token = hidden_token(page.text)
    assert client.post(url, data={**body, "csrf_token": token + "x"}).status_code == 403

    client.cookies.set("cloud_inventory_csrf", "invalid", path="/ui")
    assert client.post(url, data={**body, "csrf_token": "invalid"}).status_code == 403

    manager = CsrfManager(settings.csrf_secret.get_secret_value())
    current = time.time()
    with monkeypatch.context() as context:
        context.setattr(time, "time", lambda: current - 3 * 60 * 60)
        expired = manager.issue()
    client.cookies.set("cloud_inventory_csrf", expired, path="/ui")
    assert client.post(url, data={**body, "csrf_token": expired}).status_code == 403
