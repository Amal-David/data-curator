"""Transactional storage, worker lifecycle, and artifact integrity tests."""

import hashlib
import json
import threading
import time

import pytest

from data_curator import store as store_module
from data_curator.ingest import ValidationError, parse_upload
from data_curator.pipeline import CancelledError
from data_curator.store import ConflictError, NotFoundError, Store


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "state")
    yield instance
    instance.close()


def dataset(store, name="Local notes", role="source"):
    original = b'{"text":"Database transactions preserve the integrity of durable records.","metadata":{"input_id":"old-id"}}\n'
    return store.create_dataset(
        name,
        role,
        parse_upload(original, "local.jsonl"),
        hashlib.sha256(original).hexdigest(),
        license="MIT",
        original=original,
        filename="../../local.jsonl",
    )


def wait(store, identifier):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        run = store.get_run(identifier)
        if run["status"] in {"completed", "failed", "cancelled"}:
            return run
        time.sleep(0.01)
    raise AssertionError("Worker did not stop")


def test_original_upload_and_license_are_immutable_and_separate(store):
    created = dataset(store)
    original_path = store.root / "datasets" / created["id"] / "original.upload"
    assert hashlib.sha256(original_path.read_bytes()).hexdigest() == created["sha256"]
    loaded = store.get_dataset(created["id"])
    assert loaded["records"][0]["metadata"] == {"input_id": "old-id", "license": "MIT"}
    assert loaded["records"][0]["id"].startswith(created["id"] + ":")
    assert "records" not in store.list_datasets()[0]
    created["records"][0]["text"] = "client mutation"
    assert store.get_dataset(created["id"])["records"][0]["text"] != "client mutation"


def test_invalid_import_creates_no_artifacts(store):
    with pytest.raises(ValidationError):
        store.create_dataset("bad", "source", [{"text": None}], "a" * 64)
    assert store.list_datasets() == []
    assert not (store.root / "datasets").exists()


def test_original_checksum_mismatch_is_atomic(store):
    with pytest.raises(ValidationError, match="hash"):
        store.create_dataset("bad", "source", [{"text": "valid"}], "a" * 64, original=b"valid")
    assert not store.list_datasets()


def test_immutable_dataset_tampering_is_detected(store):
    created = dataset(store)
    (store.root / "datasets" / created["id"] / "records.jsonl").write_text('{"text":"changed"}\n')
    with pytest.raises(ConflictError, match="checksum"):
        store.start_run([created["id"]], {})
    assert not store.list_runs()


def test_run_artifacts_hashes_reconcile_and_retry_is_identical(store):
    created = dataset(store)
    first = wait(store, store.start_run([created["id"]], {})["id"])
    assert first["status"] == "completed", first
    output = store.get_artifact(first["id"], "curated.jsonl")
    assert hashlib.sha256(output).hexdigest() == first["manifest"]["output_sha256"]
    second = wait(store, store.retry_run(first["id"])["id"])
    assert second["status"] == "completed", second
    for name in Store.ARTIFACTS:
        assert store.get_artifact(first["id"], name) == store.get_artifact(second["id"], name)
    assert first["id"] != second["id"]
    assert store.get_artifact(first["id"], "generated.jsonl") == b""
    with pytest.raises(ConflictError, match="referenced"):
        store.delete_dataset(created["id"])


def test_generated_candidates_saved_even_when_not_composed(store, monkeypatch):
    original_pipeline = store_module.run_pipeline
    synthetic = {"id": "synthetic-held", "text": "Generated but unselected", "kind": "text", "metadata": {}}

    def pipeline(*args, **kwargs):
        result = original_pipeline(*args, **kwargs)
        result["generated_records"] = [synthetic]
        return result

    monkeypatch.setattr(store_module, "run_pipeline", pipeline)
    created = dataset(store)
    run = wait(store, store.start_run([created["id"]], {})["id"])
    assert json.loads(store.get_artifact(run["id"], "generated.jsonl")) == synthetic
    assert synthetic["id"] not in [record["id"] for record in store.get_run_records(run["id"])]


def test_active_worker_cancellation_and_other_store_does_not_recover_it(store, monkeypatch):
    entered = threading.Event()

    def slow_pipeline(_datasets, _recipe, *, cancel=None, progress=None):
        entered.set()
        while not cancel():
            time.sleep(0.005)
        raise CancelledError("cancelled")

    monkeypatch.setattr(store_module, "run_pipeline", slow_pipeline)
    created = dataset(store)
    run = store.start_run([created["id"]], {})
    assert entered.wait(2)
    with Store(store.root) as observer:
        assert observer.get_run(run["id"])["status"] == "running"
        with pytest.raises(ConflictError, match="Another run"):
            observer.start_run([created["id"]], {})
    with pytest.raises(ConflictError):
        store.start_run([created["id"]], {})
    store.cancel_run(run["id"])
    terminal = wait(store, run["id"])
    assert terminal["status"] == "cancelled"
    assert not (store.root / "runs" / run["id"]).exists()
    with pytest.raises(ConflictError, match="completed"):
        store.get_artifact(run["id"], "curated.jsonl")


def test_cancellation_during_artifact_writes_prevents_publication(store, monkeypatch):
    writing = threading.Event()
    release = threading.Event()
    actual_write = store_module._atomic_write

    def paused_write(path, content):
        if path.name == "curated.jsonl":
            writing.set()
            assert release.wait(3)
        return actual_write(path, content)

    monkeypatch.setattr(store_module, "_atomic_write", paused_write)
    created = dataset(store)
    run = store.start_run([created["id"]], {})
    assert writing.wait(3)
    store.cancel_run(run["id"])
    release.set()
    assert wait(store, run["id"])["status"] == "cancelled"
    assert not (store.root / "runs" / run["id"]).exists()
    assert not list((store.root / "runs").glob(".pending-*"))


def test_restart_marks_orphaned_run_failed(store):
    created = dataset(store)
    run = wait(store, store.start_run([created["id"]], {})["id"])
    store._update_run(run["id"], status="running")
    store.close()
    with Store(store.root) as restarted:
        recovered = restarted.get_run(run["id"])
        assert recovered["status"] == "failed"
        assert "interrupted" in recovered["error"]
        retried = wait(restarted, restarted.retry_run(run["id"])["id"])
        assert retried["status"] == "completed"


def test_run_preflight_bounds_and_sources(store, monkeypatch):
    created = dataset(store)
    monkeypatch.setattr(store_module, "MAX_CHARACTERS", 2)
    with pytest.raises(ValidationError, match="characters"):
        store.start_run([created["id"]], {})
    assert not store.list_runs()
    target = dataset(store, "Evaluation", "holdout")
    with pytest.raises(ValidationError, match="source"):
        store.start_run([target["id"]], {})
    with pytest.raises(ValidationError, match="unique"):
        store.start_run([created["id"], created["id"]], {})


def test_failed_pipeline_can_retry_and_no_fake_success(store, monkeypatch):
    actual = store_module.run_pipeline

    def fail(*_args, **_kwargs):
        raise RuntimeError("Local inference server unavailable")

    monkeypatch.setattr(store_module, "run_pipeline", fail)
    created = dataset(store)
    failed = wait(store, store.start_run([created["id"]], {})["id"])
    assert failed["status"] == "failed"
    assert "unavailable" in failed["error"]
    monkeypatch.setattr(store_module, "run_pipeline", actual)
    assert wait(store, store.retry_run(failed["id"])["id"])["status"] == "completed"


def test_recipe_persistence_delete_and_path_rejection(store):
    recipe = store.save_recipe("Reusable", {"seed": 9})
    assert store.list_recipes()[0] == recipe
    created = dataset(store)
    store.delete_dataset(created["id"])
    with pytest.raises(NotFoundError):
        store.get_dataset(created["id"])
    with pytest.raises(NotFoundError):
        store.get_dataset("../../etc/passwd")
    with pytest.raises(ValueError):
        store.save_recipe("bad", {"unknown": True})


def test_run_budget_is_checked_before_loading_any_dataset(store, monkeypatch):
    created = dataset(store)
    monkeypatch.setattr(store_module, "MAX_CHARACTERS", 2)

    def unexpected_load(_identifier):
        raise AssertionError("Oversized run should be rejected from metadata before reading records")

    monkeypatch.setattr(store, "get_dataset", unexpected_load)
    with pytest.raises(ValidationError, match="characters"):
        store.start_run([created["id"]], {})
