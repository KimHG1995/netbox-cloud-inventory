import hashlib
import os
import tempfile
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol
from uuid import UUID

from cloud_inventory.ingest.file_validation import ValidatedFile


@dataclass(frozen=True)
class StoredArtifact:
    object_key: str
    size_bytes: int
    sha256: str


class ArtifactStore(Protocol):
    async def put(
        self,
        validated_file: ValidatedFile,
        object_key: str,
    ) -> StoredArtifact: ...

    def open(self, object_key: str) -> AbstractAsyncContextManager[BinaryIO]: ...

    async def delete(self, object_key: str) -> None: ...


def build_artifact_key(
    import_id: UUID,
    source_file_id: UUID,
    sha256: str,
) -> str:
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise ValueError("artifact SHA-256 must be 64 lowercase hexadecimal characters")
    return f"imports/{import_id}/{source_file_id}/{sha256}"


def _object_key_parts(object_key: str) -> tuple[str, ...]:
    path = PurePosixPath(object_key)
    parts = path.parts
    if (
        path.is_absolute()
        or "\\" in object_key
        or any(part in {"", ".", ".."} for part in parts)
        or len(parts) != 4
        or parts[0] != "imports"
    ):
        raise ValueError("invalid artifact object key")
    return parts


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    async def put(
        self,
        validated_file: ValidatedFile,
        object_key: str,
    ) -> StoredArtifact:
        try:
            _object_key_parts(object_key)
            validated_file.stream.seek(0)
            content = validated_file.stream.read()
            if len(content) != validated_file.size_bytes:
                raise ValueError("validated artifact size changed before persistence")
            if hashlib.sha256(content).hexdigest() != validated_file.sha256:
                raise ValueError("validated artifact hash changed before persistence")
            self._objects[object_key] = content
            return StoredArtifact(
                object_key=object_key,
                size_bytes=validated_file.size_bytes,
                sha256=validated_file.sha256,
            )
        finally:
            validated_file.stream.close()

    @asynccontextmanager
    async def open(self, object_key: str) -> AsyncGenerator[BinaryIO, None]:
        _object_key_parts(object_key)
        try:
            content = self._objects[object_key]
        except KeyError as error:
            raise FileNotFoundError(object_key) from error
        stream = BytesIO(content)
        try:
            yield stream
        finally:
            stream.close()

    async def delete(self, object_key: str) -> None:
        _object_key_parts(object_key)
        self._objects.pop(object_key, None)


class FileSystemArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        if self.root.is_symlink():
            raise ValueError("artifact root cannot be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self._resolved_root = self.root.resolve()

    def _resolve(
        self,
        object_key: str,
        *,
        create_parents: bool,
    ) -> Path:
        parts = _object_key_parts(object_key)
        parent = self.root
        for part in parts[:-1]:
            parent = parent / part
            if parent.is_symlink():
                raise ValueError("artifact path cannot contain a symlink")
            if create_parents:
                parent.mkdir(mode=0o700, exist_ok=True)
            if parent.exists() and not parent.is_dir():
                raise ValueError("artifact parent path must be a directory")

        candidate = parent / parts[-1]
        if candidate.is_symlink():
            raise ValueError("artifact path cannot be a symlink")
        try:
            candidate.resolve(strict=False).relative_to(self._resolved_root)
        except ValueError as error:
            raise ValueError("artifact object key resolves outside the root") from error
        return candidate

    async def put(
        self,
        validated_file: ValidatedFile,
        object_key: str,
    ) -> StoredArtifact:
        temporary_path: Path | None = None
        try:
            target = self._resolve(object_key, create_parents=True)
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                dir=target.parent,
            )
            temporary_path = Path(temporary_name)
            digest = hashlib.sha256()
            size_bytes = 0
            try:
                os.fchmod(file_descriptor, 0o600)
            except Exception:
                os.close(file_descriptor)
                raise
            with os.fdopen(file_descriptor, "wb") as destination:
                validated_file.stream.seek(0)
                while chunk := validated_file.stream.read(1024 * 1024):
                    destination.write(chunk)
                    digest.update(chunk)
                    size_bytes += len(chunk)
                destination.flush()
                os.fsync(destination.fileno())

            if (
                size_bytes != validated_file.size_bytes
                or digest.hexdigest() != validated_file.sha256
            ):
                raise ValueError("validated artifact changed before persistence")
            os.replace(temporary_path, target)
            temporary_path = None
            directory_descriptor = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            return StoredArtifact(
                object_key=object_key,
                size_bytes=size_bytes,
                sha256=digest.hexdigest(),
            )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            validated_file.stream.close()

    @asynccontextmanager
    async def open(self, object_key: str) -> AsyncGenerator[BinaryIO, None]:
        target = self._resolve(object_key, create_parents=False)
        source = target.open("rb")
        try:
            yield source
        finally:
            source.close()

    async def delete(self, object_key: str) -> None:
        target = self._resolve(object_key, create_parents=False)
        target.unlink(missing_ok=True)
