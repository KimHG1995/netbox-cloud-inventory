import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook
from pytest import MonkeyPatch

from cloud_inventory.config import get_settings


@pytest.fixture(autouse=True)
def deterministic_test_settings(monkeypatch: MonkeyPatch) -> Iterator[None]:
    defaults = {
        "INVENTORY_DATABASE_URL": (
            "postgresql+psycopg://inventory:inventory@localhost:5432/inventory"
        ),
        "INVENTORY_NETBOX_TOKEN": "test-token",
        "INVENTORY_CSRF_SECRET": "test-csrf-secret",
    }
    for name, value in defaults.items():
        if name not in os.environ:
            monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def write_xlsx():
    def writer(path: Path, headers: list[str], rows: list[list[Any]]) -> Path:
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        workbook.save(path)
        workbook.close()
        return path

    return writer
