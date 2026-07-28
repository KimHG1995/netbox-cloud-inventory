import hashlib
import os
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest

from cloud_inventory.ingest.artifact_store import (
    FileSystemArtifactStore,
    InMemoryArtifactStore,
    build_artifact_key,
)
from cloud_inventory.ingest.file_validation import validate_upload

CONTENT = b"Identifier,Resource type\nitem,ec2:instance\n"
SHA256 = hashlib.sha256(CONTENT).hexdigest()
IMPORT_ID = UUID("11111111-1111-1111-1111-111111111111")
SOURCE_FILE_ID = UUID("22222222-2222-2222-2222-222222222222")


def validated_csv():
    return validate_upload(BytesIO(CONTENT), "export.csv", 1024)


def object_key() -> str:
    return build_artifact_key(IMPORT_ID, SOURCE_FILE_ID, SHA256)


@pytest.mark.asyncio
async def test_in_memory_store_round_trip_and_idempotent_delete() -> None:
    store = InMemoryArtifactStore()
    validated = validated_csv()

    stored = await store.put(validated, object_key())

    assert validated.stream.closed
    assert stored.object_key == object_key()
    assert stored.sha256 == SHA256
    async with store.open(object_key()) as source:
        assert source.read() == CONTENT
    await store.delete(object_key())
    await store.delete(object_key())


@pytest.mark.asyncio
async def test_filesystem_store_uses_private_atomic_files(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    store = FileSystemArtifactStore(root)

    stored = await store.put(validated_csv(), object_key())

    stored_path = root.joinpath(*stored.object_key.split("/"))
    assert stored_path.read_bytes() == CONTENT
    assert os.stat(root).st_mode & 0o777 == 0o700
    assert os.stat(stored_path).st_mode & 0o777 == 0o600
    async with store.open(object_key()) as source:
        assert source.read() == CONTENT


@pytest.mark.asyncio
async def test_filesystem_store_rejects_absolute_and_parent_paths(
    tmp_path: Path,
) -> None:
    store = FileSystemArtifactStore(tmp_path / "artifacts")

    for unsafe_key in ("/absolute/key", "../outside"):
        with pytest.raises(ValueError, match="object key"):
            await store.put(validated_csv(), unsafe_key)


@pytest.mark.asyncio
async def test_filesystem_store_rejects_symlinked_parent(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "imports").mkdir(parents=True)
    (root / "imports" / str(IMPORT_ID)).symlink_to(outside, target_is_directory=True)
    store = FileSystemArtifactStore(root)

    with pytest.raises(ValueError, match="symlink"):
        await store.put(validated_csv(), object_key())


def test_object_key_never_contains_original_filename() -> None:
    key = build_artifact_key(IMPORT_ID, SOURCE_FILE_ID, SHA256)

    assert key == f"imports/{IMPORT_ID}/{SOURCE_FILE_ID}/{SHA256}"
    assert "export.csv" not in key
