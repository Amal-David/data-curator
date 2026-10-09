"""Local immutable datasets, transactional run history, and atomic artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .ingest import MAX_UPLOAD_BYTES, ValidationError, validate_records
from .pipeline import MAX_CHARACTERS, MAX_RECORDS, CancelledError, export_jsonl, run_pipeline, validate_recipe


class NotFoundError(LookupError):
    """The requested resource does not exist."""


class ConflictError(RuntimeError):
    """The requested mutation conflicts with persisted state."""


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise NotFoundError("Resource not found")
    return value


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _acquire_run_lock(path: Path):
    """A process-wide lease prevents a second CLI/server from recovering live runs."""
    handle = path.open("a+b")
    try:
        if os.name == "nt":  # pragma: no cover - exercised on Windows installations
            import msvcrt

            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


class Store:
    """SQLite metadata plus immutable original and per-run derived artifacts.

    This first version intentionally materializes bounded datasets and runs in
    memory. There is one worker across all processes sharing a data directory.
    """

    ARTIFACTS = frozenset(
        {
            "curated.jsonl",
            "rejected.jsonl",
            "generated.jsonl",
            "decisions.jsonl",
            "manifest.json",
            "recipe.json",
            "report.json",
        }
    )

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "curator.sqlite3"
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._active_id: str | None = None
        self._closed = False
        with self._connect() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS run_datasets (run_id TEXT NOT NULL REFERENCES runs(id), dataset_id TEXT NOT NULL REFERENCES datasets(id), PRIMARY KEY(run_id,dataset_id));
                CREATE TABLE IF NOT EXISTS recipes (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs ((1)) WHERE status IN ('queued','running');
            """)
        lease = _acquire_run_lock(self.root / "worker.lock")
        if lease is not None:
            try:
                with self._connect() as connection:
                    for row in connection.execute(
                        "SELECT payload FROM runs WHERE status IN ('queued','running')"
                    ).fetchall():
                        run = json.loads(row[0])
                        run.update(
                            status="failed",
                            error="Run interrupted before completion. Retry to create a fresh run.",
                        )
                        run["progress"]["message"] = "Interrupted; retry available"
                        connection.execute(
                            "UPDATE runs SET status=?,payload=? WHERE id=?",
                            (run["status"], _json(run), run["id"]),
                        )
            finally:
                lease.close()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def create_dataset(
        self,
        name: str,
        role: str,
        records: list[dict[str, Any]],
        sha256: str,
        *,
        license: str = "unknown",
        original: bytes | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValidationError("Dataset name must contain 1–160 characters")
        if role not in ("source", "target", "holdout"):
            raise ValidationError("Dataset role must be source, target, or holdout")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValidationError("Dataset sha256 must be a lowercase SHA-256 hex digest")
        if not isinstance(license, str) or not license.strip() or len(license) > 200:
            raise ValidationError("License must contain 1–200 characters")
        if filename is not None and (not isinstance(filename, str) or len(filename) > 1024):
            raise ValidationError("Filename must contain at most 1,024 characters")
        if original is not None:
            if not isinstance(original, bytes) or len(original) > MAX_UPLOAD_BYTES:
                raise ValidationError("Original upload must be bytes no larger than 20 MiB")
            if hashlib.sha256(original).hexdigest() != sha256:
                raise ValidationError("Original upload hash does not match sha256")
        # Do not reinterpret parser-assigned IDs as externally supplied IDs.
        source_records = copy.deepcopy(records)
        if isinstance(source_records, list):
            for record in source_records:
                if isinstance(record, dict):
                    record.pop("id", None)
        validated = validate_records(source_records)
        identifier = uuid.uuid4().hex
        for index, record in enumerate(validated):
            record["id"] = f"{identifier}:{index + 1:06d}"
            record["metadata"].setdefault("license", license.strip())
        encoded = export_jsonl(validated)
        if len(encoded) > 40 * 1024 * 1024:
            raise ValidationError("Parsed dataset exceeds the 40 MiB storage limit")
        dataset = {
            "id": identifier,
            "name": name.strip(),
            "role": role,
            "sha256": sha256,
            "records_sha256": hashlib.sha256(encoded).hexdigest(),
            "created_at": _timestamp(),
            "record_count": len(validated),
            "text_char_count": sum(len(record["text"]) for record in validated),
            "storage_bytes": len(encoded),
            "license": license.strip(),
            "declared_license": license.strip(),
            "filename": filename,
            "records": validated,
        }
        location = self.root / "datasets" / identifier
        try:
            _atomic_write(location / "records.jsonl", encoded)
            if original is not None:
                _atomic_write(location / "original.upload", original)
            _atomic_write(
                location / "metadata.json",
                _json({k: v for k, v in dataset.items() if k != "records"}).encode("utf-8"),
            )
            with self._lock, self._connect() as connection:
                connection.execute(
                    "INSERT INTO datasets VALUES (?,?)",
                    (identifier, _json({k: v for k, v in dataset.items() if k != "records"})),
                )
        except Exception:
            shutil.rmtree(location, ignore_errors=True)
            raise
        return dataset

    def list_datasets(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute("SELECT payload FROM datasets ORDER BY rowid")
            ]

    def _dataset_metadata(self, identifier: str) -> dict[str, Any]:
        identifier = _identifier(identifier)
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM datasets WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise NotFoundError("Dataset not found")
        return json.loads(row[0])

    def get_dataset(self, identifier: str) -> dict[str, Any]:
        dataset = self._dataset_metadata(identifier)
        path = self.root / "datasets" / identifier / "records.jsonl"
        if path.stat().st_size > 40 * 1024 * 1024:
            raise ConflictError("Immutable dataset exceeds its storage limit; restore original data")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != dataset["records_sha256"]:
            raise ConflictError("Immutable dataset checksum mismatch; restore original data before running")
        dataset["records"] = [json.loads(line) for line in raw.splitlines() if line]
        return dataset

    def delete_dataset(self, identifier: str) -> None:
        identifier = _identifier(identifier)
        with self._lock, self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM run_datasets WHERE dataset_id=? LIMIT 1", (identifier,)
            ).fetchone():
                raise ConflictError("Dataset is referenced by a persisted run and cannot be deleted")
            cursor = connection.execute("DELETE FROM datasets WHERE id=?", (identifier,))
            if not cursor.rowcount:
                raise NotFoundError("Dataset not found")
        shutil.rmtree(self.root / "datasets" / identifier, ignore_errors=True)

    def list_runs(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute("SELECT payload FROM runs ORDER BY rowid DESC")
            ]

    def get_run(self, identifier: str) -> dict[str, Any]:
        identifier = _identifier(identifier)
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM runs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise NotFoundError("Run not found")
        return json.loads(row[0])

    def _update_run(self, identifier: str, **values: Any) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT payload FROM runs WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise NotFoundError("Run not found")
            run = json.loads(row[0])
            run.update(values)
            connection.execute(
                "UPDATE runs SET status=?,payload=? WHERE id=?", (run["status"], _json(run), identifier)
            )
        return run

    def start_run(self, dataset_ids: list[str], recipe: dict[str, Any]) -> dict[str, Any]:
        if (
            not isinstance(dataset_ids, list)
            or not dataset_ids
            or len(dataset_ids) > 100
            or any(not isinstance(value, str) for value in dataset_ids)
        ):
            raise ValidationError("Select 1–100 dataset IDs")
        if len(set(dataset_ids)) != len(dataset_ids):
            raise ValidationError("Dataset IDs must be unique")
        normalized = validate_recipe(recipe)
        with self._lock:
            if self._closed:
                raise ConflictError("Store is closed")
            lease = _acquire_run_lock(self.root / "worker.lock")
            if lease is None:
                raise ConflictError("Another run is active in this data directory")
            try:
                metadata = [self._dataset_metadata(identifier) for identifier in dataset_ids]
                if not any(dataset["role"] == "source" for dataset in metadata):
                    raise ValidationError("At least one source dataset is required")
                if sum(dataset["record_count"] for dataset in metadata) > MAX_RECORDS:
                    raise ValidationError("A local run is limited to 50,000 total input records")
                if sum(dataset.get("text_char_count", 0) for dataset in metadata) > MAX_CHARACTERS:
                    raise ValidationError("A local run is limited to 20,000,000 total input characters")
                datasets = []
                total_characters = 0
                for dataset_id in dataset_ids:
                    dataset = self.get_dataset(dataset_id)
                    total_characters += sum(len(record["text"]) for record in dataset["records"])
                    if total_characters > MAX_CHARACTERS:
                        raise ValidationError("A local run is limited to 20,000,000 total input characters")
                    datasets.append(dataset)
                identifier = uuid.uuid4().hex
                run = {
                    "id": identifier,
                    "status": "queued",
                    "created_at": _timestamp(),
                    "recipe": normalized,
                    "dataset_ids": dataset_ids[:],
                    "progress": {
                        "stage": "queued",
                        "completed": 0,
                        "total": 0,
                        "message": "Waiting for local worker",
                    },
                    "summary": None,
                    "manifest": None,
                    "error": None,
                }
                with self._connect() as connection:
                    connection.execute("INSERT INTO runs VALUES (?,?,?)", (identifier, "queued", _json(run)))
                    for dataset_id in dataset_ids:
                        connection.execute("INSERT INTO run_datasets VALUES (?,?)", (identifier, dataset_id))
                self._cancel = threading.Event()
                self._active_id = identifier
                self._worker = threading.Thread(
                    target=self._execute,
                    args=(run, datasets, lease, self._cancel),
                    daemon=True,
                    name=f"curator-{identifier[:8]}",
                )
                self._worker.start()
                return run
            except sqlite3.IntegrityError as exc:
                lease.close()
                raise ConflictError("Another run is active in this data directory") from exc
            except Exception:
                lease.close()
                raise

    create_run = start_run

    def _execute(
        self, run: dict[str, Any], datasets: list[dict[str, Any]], lease: Any, cancel: threading.Event
    ) -> None:
        identifier = run["id"]
        temporary: Path | None = None
        last_progress = 0.0
        last_stage = ""

        def progress(value: dict[str, Any]) -> None:
            nonlocal last_progress, last_stage
            now = time.monotonic()
            if value.get("stage") != last_stage or now - last_progress >= 0.15:
                self._update_run(identifier, progress=value)
                last_progress, last_stage = now, value.get("stage", "")

        try:
            if cancel.is_set():
                raise CancelledError("Run cancelled")
            self._update_run(identifier, status="running")
            result = run_pipeline(datasets, run["recipe"], cancel=cancel.is_set, progress=progress)
            if cancel.is_set():
                raise CancelledError("Run cancelled")
            runs_root = self.root / "runs"
            runs_root.mkdir(exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix=".pending-", dir=runs_root))
            artifacts = {
                "curated.jsonl": export_jsonl(result["records"]),
                "rejected.jsonl": export_jsonl(result.get("rejected_records", [])),
                "generated.jsonl": export_jsonl(result.get("generated_records", [])),
                "decisions.jsonl": export_jsonl(result["decisions"]),
                "manifest.json": (_json(result["manifest"]) + "\n").encode("utf-8"),
                "recipe.json": (_json(run["recipe"]) + "\n").encode("utf-8"),
                "report.json": (_json(result["summary"]) + "\n").encode("utf-8"),
            }
            for name, content in artifacts.items():
                _atomic_write(temporary / name, content)
            # Original inputs and generated candidates are retained independently of the final mixture.
            _atomic_write(temporary / "input_snapshot.json", (_json(datasets) + "\n").encode("utf-8"))
            with self._lock:
                if cancel.is_set():
                    raise CancelledError("Run cancelled")
                os.replace(temporary, runs_root / identifier)
                temporary = None
                self._update_run(
                    identifier,
                    status="completed",
                    summary=result["summary"],
                    manifest=result["manifest"],
                    progress={
                        "stage": "completed",
                        "completed": len(result["records"]),
                        "total": len(result["records"]),
                        "message": "Artifacts saved locally",
                    },
                )
        except CancelledError:
            self._update_run(
                identifier,
                status="cancelled",
                error=None,
                progress={
                    "stage": "cancelled",
                    "completed": 0,
                    "total": 0,
                    "message": "Run cancelled; originals preserved",
                },
            )
        except Exception as exc:
            self._update_run(
                identifier,
                status="failed",
                error=f"{type(exc).__name__}: {str(exc)[:1000]}",
                progress={
                    "stage": "failed",
                    "completed": 0,
                    "total": 0,
                    "message": "Run failed; originals preserved",
                },
            )
        finally:
            if temporary is not None:
                shutil.rmtree(temporary, ignore_errors=True)
            lease.close()
            with self._lock:
                if self._active_id == identifier:
                    self._active_id = None

    def cancel_run(self, identifier: str) -> dict[str, Any]:
        with self._lock:
            run = self.get_run(identifier)
            if run["status"] not in {"queued", "running"}:
                return run
            if self._active_id != identifier:
                raise ConflictError("Run belongs to another worker; cancel it in the process that started it")
            self._cancel.set()
            return self._update_run(
                identifier, progress={**run["progress"], "message": "Cancellation requested"}
            )

    def retry_run(self, identifier: str) -> dict[str, Any]:
        run = self.get_run(identifier)
        if run["status"] in {"queued", "running"}:
            raise ConflictError("Wait for the active run to finish or cancel it before retrying")
        return self.start_run(run["dataset_ids"], run["recipe"])

    def get_artifact(self, identifier: str, name: str) -> bytes:
        run = self.get_run(identifier)
        if name not in self.ARTIFACTS:
            raise NotFoundError("Artifact not found")
        if run["status"] != "completed":
            raise ConflictError("Artifacts are available only for completed runs")
        return (self.root / "runs" / identifier / name).read_bytes()

    def get_run_records(self, identifier: str) -> list[dict[str, Any]]:
        return [
            json.loads(line) for line in self.get_artifact(identifier, "curated.jsonl").splitlines() if line
        ]

    def get_run_decisions(self, identifier: str) -> list[dict[str, Any]]:
        return [
            json.loads(line) for line in self.get_artifact(identifier, "decisions.jsonl").splitlines() if line
        ]

    def get_run_rejected(self, identifier: str) -> list[dict[str, Any]]:
        return [
            json.loads(line) for line in self.get_artifact(identifier, "rejected.jsonl").splitlines() if line
        ]

    def save_recipe(self, name: str, recipe: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValidationError("Recipe name must contain 1–160 characters")
        result = {"id": uuid.uuid4().hex, "name": name.strip(), "recipe": validate_recipe(recipe)}
        with self._connect() as connection:
            connection.execute("INSERT INTO recipes VALUES (?,?)", (result["id"], _json(result)))
        return result

    def list_recipes(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute("SELECT payload FROM recipes ORDER BY rowid DESC")
            ]

    def close(self) -> None:
        self._closed = True
        self._cancel.set()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=5)

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
