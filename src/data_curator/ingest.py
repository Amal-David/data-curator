"""Bounded, non-executing parsers for local dataset uploads."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from pathlib import PurePath
from typing import Any

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_RECORDS = 10_000
MAX_RECORD_CHARS = 200_000
MAX_METADATA_BYTES = 16_384
MAX_METADATA_DEPTH = 8
CODE_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".go",
        ".rs",
        ".rb",
        ".sh",
        ".sql",
        ".swift",
        ".kt",
        ".scala",
        ".r",
        ".lua",
        ".html",
        ".css",
        ".toml",
        ".yaml",
        ".yml",
    }
)


class ValidationError(ValueError):
    """An upload is not a valid bounded dataset."""


def _reject_constant(value: str) -> None:
    raise ValidationError(f"Non-finite JSON number is not allowed: {value}")


def _json(text: str, location: str) -> Any:
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except ValidationError:
        raise
    except (ValueError, RecursionError) as exc:
        raise ValidationError(f"{location}: invalid JSON ({str(exc)[:160]})") from exc


def _check_value(value: Any, depth: int = 0) -> None:
    if depth > MAX_METADATA_DEPTH:
        raise ValidationError(f"Metadata nesting exceeds {MAX_METADATA_DEPTH} levels")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValidationError("Metadata keys must be strings")
            _check_value(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _check_value(child, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValidationError("Metadata cannot contain non-finite numbers")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValidationError("Metadata must contain JSON-compatible values")


def validate_records(records: Any) -> list[dict[str, Any]]:
    """Validate caller-supplied records as strictly as uploaded records."""
    if not isinstance(records, list) or not records:
        raise ValidationError("Dataset must contain at least one record")
    if len(records) > MAX_RECORDS:
        raise ValidationError(f"Dataset exceeds {MAX_RECORDS:,} records")
    clean = []
    for number, record in enumerate(records, 1):
        clean.append(_record(record, number, "text", "text"))
    return clean


def _record(value: Any, number: int, text_field: str, default_kind: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"Record {number}: expected an object")
    text = value.get(text_field)
    if not isinstance(text, str):
        raise ValidationError(f"Record {number}: field '{text_field}' must be a string")
    if len(text) > MAX_RECORD_CHARS:
        raise ValidationError(f"Record {number}: text exceeds {MAX_RECORD_CHARS:,} characters")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValidationError(f"Record {number}: text contains invalid Unicode") from exc
    kind = value.get("kind", default_kind)
    if kind not in ("text", "code"):
        raise ValidationError(f"Record {number}: kind must be 'text' or 'code'")
    metadata = value.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValidationError(f"Record {number}: metadata must be an object")
    metadata = dict(metadata)
    # Preserve extra columns without ever treating user IDs as filesystem names.
    for key, extra in value.items():
        if key not in {text_field, "kind", "metadata", "id"}:
            metadata.setdefault(key, extra)
    if "id" in value and "input_id" not in metadata:
        metadata["input_id"] = value["id"]
    _check_value(metadata)
    try:
        encoded = json.dumps(metadata, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValidationError(f"Record {number}: invalid metadata") from exc
    if len(encoded) > MAX_METADATA_BYTES:
        raise ValidationError(f"Record {number}: metadata exceeds {MAX_METADATA_BYTES:,} bytes")
    # The Store assigns dataset-scoped IDs; this content ID is useful to pure parser callers.
    identifier = hashlib.sha256((str(number) + "\0" + kind + "\0" + text).encode("utf-8")).hexdigest()[:24]
    return {"id": identifier, "text": text, "kind": kind, "metadata": metadata}


def parse_upload(
    content: bytes, filename: str, *, text_field: str = "text", kind: str = "text"
) -> list[dict[str, Any]]:
    """Parse UTF-8 JSONL/JSON/CSV or one plain-text/code document, atomically.

    Filenames are used only to select a parser. They are never opened, written, or
    executed. Code text is preserved byte-for-byte after UTF-8 decoding (except BOM).
    """
    if not isinstance(content, bytes):
        raise ValidationError("Upload content must be bytes")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValidationError(f"Upload exceeds {MAX_UPLOAD_BYTES // 1024 // 1024} MiB limit")
    if not isinstance(filename, str) or not filename or len(filename) > 1024:
        raise ValidationError("A filename of at most 1,024 characters is required")
    if not isinstance(text_field, str) or not text_field or len(text_field) > 128:
        raise ValidationError("text_field must be a nonempty string of at most 128 characters")
    if kind not in ("text", "code"):
        raise ValidationError("kind must be 'text' or 'code'")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("Upload must be valid UTF-8 text") from exc
    if not decoded.strip():
        raise ValidationError("Upload is empty")
    suffix = PurePath(filename).suffix.lower()
    parsed: list[dict[str, Any]] = []

    def append(value: Any, location: int) -> None:
        if len(parsed) >= MAX_RECORDS:
            raise ValidationError(f"Dataset exceeds {MAX_RECORDS:,} records")
        parsed.append(_record(value, location, text_field, kind))

    if suffix in {".jsonl", ".ndjson"}:
        for line_number, line in enumerate(io.StringIO(decoded), 1):
            if line.strip():
                append(_json(line, f"Line {line_number}"), line_number)
    elif suffix == ".json":
        values = _json(decoded, "Upload")
        if not isinstance(values, list):
            raise ValidationError("JSON upload must be an array of record objects")
        for number, value in enumerate(values, 1):
            append(value, number)
    elif suffix == ".csv":
        try:
            csv.field_size_limit(MAX_RECORD_CHARS)
            reader = csv.DictReader(io.StringIO(decoded, newline=""), strict=True)
            if not reader.fieldnames or text_field not in reader.fieldnames:
                raise ValidationError(f"CSV requires a '{text_field}' column")
            if len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValidationError("CSV column names must be unique")
            for number, value in enumerate(reader, 2):
                if None in value or any(item is None for item in value.values()):
                    raise ValidationError(f"CSV row {number}: column count does not match header")
                if "metadata" in value:
                    value["metadata"] = _json(value["metadata"] or "{}", f"CSV row {number} metadata")
                if value.get("kind") == "":
                    value.pop("kind")
                append(value, number)
        except csv.Error as exc:
            raise ValidationError(f"Invalid CSV: {exc}") from exc
    elif suffix in {".txt", ".md", ".markdown"} or suffix in CODE_EXTENSIONS:
        default_kind = "code" if suffix in CODE_EXTENSIONS else kind
        append({text_field: decoded, "kind": default_kind}, 1)
    else:
        raise ValidationError(
            "Unsupported file type. Use JSONL, JSON, CSV, TXT, Markdown, or a common code file"
        )
    if not parsed:
        raise ValidationError("Dataset must contain at least one record")
    return parsed
