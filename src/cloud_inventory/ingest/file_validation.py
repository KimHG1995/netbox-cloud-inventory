import csv
import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, cast
from zipfile import BadZipFile, ZipFile

MediaType = Literal[
    "text/csv",
    "application/json",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
]

_CHUNK_SIZE = 1024 * 1024
_SPOOL_MEMORY_LIMIT = 8 * 1024 * 1024
_XLSX_MEDIA_TYPE: MediaType = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
_EXECUTABLE_SIGNATURES = (
    b"MZ",
    b"\x7fELF",
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe",
)


@dataclass
class ValidatedFile:
    stream: BinaryIO
    original_filename: str
    media_type: MediaType
    size_bytes: int
    sha256: str


def _validate_filename(filename: str) -> str:
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or Path(filename).is_absolute()
        or Path(filename).name != filename
    ):
        raise ValueError("upload filename must be a safe basename")
    if Path(filename).suffix.casefold() == ".xls":
        raise ValueError("legacy .xls workbooks are not supported")
    return filename


def _detect_xlsx(stream: BinaryIO) -> MediaType:
    stream.seek(0)
    try:
        with ZipFile(stream) as archive:
            names = {
                entry.filename.replace("\\", "/").casefold()
                for entry in archive.infolist()
            }
            if any(name.endswith("vbaproject.bin") for name in names):
                raise ValueError("macro-enabled workbooks are not allowed")
            if {"encryptioninfo", "encryptedpackage"} & names:
                raise ValueError("encrypted workbooks are not supported")
            required = {"[content_types].xml", "xl/workbook.xml"}
            has_worksheet = any(
                name.startswith("xl/worksheets/") and name.endswith(".xml")
                for name in names
            )
            if not required.issubset(names) or not has_worksheet:
                raise ValueError("ZIP upload is not a valid XLSX workbook")
    except BadZipFile as error:
        raise ValueError("ZIP upload is not a valid XLSX workbook") from error
    return _XLSX_MEDIA_TYPE


def _first_non_whitespace_byte(stream: BinaryIO) -> bytes:
    stream.seek(0)
    while chunk := stream.read(64 * 1024):
        for value in chunk:
            if value not in b" \t\r\n":
                return bytes([value])
    return b""


def _detect_text_media_type(stream: BinaryIO) -> MediaType:
    first_byte = _first_non_whitespace_byte(stream)
    if first_byte in {b"{", b"["}:
        return "application/json"

    stream.seek(0)
    sample = stream.read(64 * 1024)
    try:
        text = sample.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("unsupported binary upload") from error
    if "\x00" in text:
        raise ValueError("unsupported binary upload")

    first_line = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = next(
        (candidate for candidate in (",", "\t", ";") if candidate in first_line),
        None,
    )
    if delimiter is None:
        raise ValueError("unsupported upload content")
    try:
        columns = next(csv.reader([first_line], delimiter=delimiter))
    except csv.Error as error:
        raise ValueError("unsupported CSV content") from error
    if len(columns) < 2:
        raise ValueError("unsupported CSV content")
    return "text/csv"


def _detect_media_type(stream: BinaryIO) -> MediaType:
    stream.seek(0)
    signature = stream.read(4)
    if any(signature.startswith(prefix) for prefix in _EXECUTABLE_SIGNATURES):
        raise ValueError("executable uploads are not allowed")
    if signature.startswith(b"PK\x03\x04"):
        return _detect_xlsx(stream)
    return _detect_text_media_type(stream)


def validate_upload(
    stream: BinaryIO,
    filename: str,
    max_bytes: int,
) -> ValidatedFile:
    safe_filename = _validate_filename(filename)
    temporary = cast(
        BinaryIO,
        tempfile.SpooledTemporaryFile(  # noqa: SIM115
            max_size=_SPOOL_MEMORY_LIMIT,
            mode="w+b",
        ),
    )
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        while chunk := stream.read(_CHUNK_SIZE):
            size_bytes += len(chunk)
            if size_bytes > max_bytes:
                raise ValueError(f"upload exceeds size limit of {max_bytes} bytes")
            digest.update(chunk)
            temporary.write(chunk)
        if size_bytes == 0:
            raise ValueError("empty uploads are not supported")

        media_type = _detect_media_type(temporary)
        temporary.seek(0)
        return ValidatedFile(
            stream=temporary,
            original_filename=safe_filename,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
        )
    except Exception:
        temporary.close()
        raise
