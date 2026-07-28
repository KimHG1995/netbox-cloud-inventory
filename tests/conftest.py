from collections.abc import Iterator

import pytest
from pytest import MonkeyPatch

from cloud_inventory.config import get_settings


@pytest.fixture(autouse=True)
def deterministic_test_settings(monkeypatch: MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(
        "INVENTORY_DATABASE_URL",
        "postgresql+psycopg://inventory:inventory@localhost:5432/inventory",
    )
    monkeypatch.setenv("INVENTORY_NETBOX_TOKEN", "test-token")
    monkeypatch.setenv("INVENTORY_CSRF_SECRET", "test-csrf-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
