import hashlib
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from cloud_inventory.ingest.file_validation import validate_upload

MEBIBYTE = 1024 * 1024
MAX_BYTES = 100 * MEBIBYTE


class GeneratedCsvStream:
    def __init__(self, size: int) -> None:
        self.remaining = size
        self.position = 0
        self.prefix = b"Identifier,Resource type\n"

    def read(self, size: int = -1) -> bytes:
        if self.remaining == 0:
            return b""
        requested = self.remaining if size < 0 else min(size, self.remaining)
        start = self.position
        self.position += requested
        self.remaining -= requested
        chunk = bytearray(b"a" * requested)
        overlap = max(0, min(self.position, len(self.prefix)) - start)
        if overlap:
            chunk[:overlap] = self.prefix[start : start + overlap]
        return bytes(chunk)


def test_exact_100_mb_is_accepted() -> None:
    validated = validate_upload(
        GeneratedCsvStream(MAX_BYTES),
        "resource-explorer.csv",
        MAX_BYTES,
    )
    try:
        assert validated.size_bytes == MAX_BYTES
        assert validated.media_type == "text/csv"
    finally:
        validated.stream.close()


def test_100_mb_plus_one_byte_is_rejected_without_reading_to_eof() -> None:
    stream = GeneratedCsvStream(MAX_BYTES + 1)

    with pytest.raises(ValueError, match="size limit"):
        validate_upload(stream, "resource-explorer.csv", MAX_BYTES)

    assert stream.position <= MAX_BYTES + MEBIBYTE


def test_csv_is_detected_from_decoded_content_not_extension() -> None:
    content = b"Identifier,Resource type\nitem,ec2:instance\n"

    validated = validate_upload(BytesIO(content), "renamed.data", 1024)
    try:
        assert validated.media_type == "text/csv"
        assert validated.sha256 == hashlib.sha256(content).hexdigest()
        assert validated.size_bytes == len(content)
    finally:
        validated.stream.close()


def test_json_is_detected_from_first_non_whitespace_byte() -> None:
    validated = validate_upload(
        BytesIO(b"\n \t {\"schema_version\":\"1\"}"),
        "renamed.data",
        1024,
    )
    try:
        assert validated.media_type == "application/json"
    finally:
        validated.stream.close()


def test_xlsx_requires_real_workbook_structure(
    tmp_path: Path,
    write_xlsx,
) -> None:
    workbook_path = write_xlsx(
        tmp_path / "export.xlsx",
        ["Server Name", "Instance ID"],
        [["server", "server-001"]],
    )

    with workbook_path.open("rb") as source:
        validated = validate_upload(source, "renamed.data", MEBIBYTE)
    try:
        assert (
            validated.media_type
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    finally:
        validated.stream.close()


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("legacy.xls", b"a,b\n1,2\n", r"\.xls"),
        ("encrypted.xlsx", b"not-a-zip", "unsupported"),
        ("program.exe", b"MZ\x90\x00binary", "executable"),
        ("../export.csv", b"a,b\n1,2\n", "filename"),
        ("folder/export.csv", b"a,b\n1,2\n", "filename"),
    ],
)
def test_unsafe_uploads_are_rejected(
    filename: str,
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_upload(BytesIO(content), filename, 1024)


def test_macro_workbook_is_rejected(
    tmp_path: Path,
    write_xlsx,
) -> None:
    workbook_path = write_xlsx(
        tmp_path / "macro.xlsx",
        ["Name", "Date Created"],
        [["bucket", "2026-07-01"]],
    )
    with ZipFile(workbook_path, "a", ZIP_DEFLATED) as archive:
        archive.writestr("xl/vbaProject.bin", b"macro")

    with workbook_path.open("rb") as source, pytest.raises(ValueError, match="macro"):
        validate_upload(source, "macro.xlsx", MEBIBYTE)
