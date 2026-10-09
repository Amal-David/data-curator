"""VM audit regressions for HTTP boundaries and durable run lifecycle."""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_integration import live_api as live_api
from test_integration import running_api
from test_store import dataset, wait

from data_curator import store as store_module
from data_curator.cli import ARTIFACTS, _export
from data_curator.server import create_server
from data_curator.store import ConflictError, Store


@pytest.mark.parametrize("name", sorted(Store.ARTIFACTS))
def test_completed_artifact_tampering_is_rejected(tmp_path, name):
    with Store(tmp_path / "state") as store:
        source = dataset(store)
        run = wait(store, store.start_run([source["id"]], {})["id"])
        assert run["status"] == "completed"
        (store.root / "runs" / run["id"] / name).write_bytes(b"changed after completion\n")
        with pytest.raises(ConflictError, match="checksum|integrity"):
            store.get_artifact(run["id"], name)


def test_missing_immutable_dataset_is_a_conflict(live_api):
    source = live_api.upload("Original", [{"text": "Preserve immutable input records."}])
    (live_api.server.store.root / "datasets" / source["id"] / "records.jsonl").unlink()
    status, error, _ = live_api.request(f"/api/datasets/{source['id']}")
    assert status == 409, error
    assert "immutable" in error["error"].lower()


def test_missing_completed_artifact_is_a_conflict(live_api):
    source = live_api.upload("Original", [{"text": "Preserve immutable output records for inspection."}])
    run = live_api.ok("/api/runs", "POST", {"dataset_ids": [source["id"]]})
    assert live_api.wait(run["id"])["status"] == "completed"
    (live_api.server.store.root / "runs" / run["id"] / "curated.jsonl").unlink()
    assert live_api.request(f"/api/runs/{run['id']}/export")[0] == 409


def test_mutating_returned_run_cannot_change_worker_recipe(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    actual = store_module.run_pipeline

    def paused(datasets, recipe, **kwargs):
        entered.set()
        assert release.wait(3)
        return actual(datasets, recipe, **kwargs)

    monkeypatch.setattr(store_module, "run_pipeline", paused)
    with Store(tmp_path / "state") as store:
        source = dataset(store)
        run = store.start_run([source["id"]], {"seed": 37})
        assert entered.wait(2)
        run["recipe"]["seed"] = 99
        release.set()
        done = wait(store, run["id"])
        assert done["status"] == "completed"
        assert json.loads(store.get_artifact(run["id"], "recipe.json"))["seed"] == 37
        assert done["recipe"]["seed"] == 37


def test_thread_start_failure_does_not_leave_a_permanent_active_run(tmp_path, monkeypatch):
    with Store(tmp_path / "state") as store:
        source = dataset(store)
        with monkeypatch.context() as patched:

            def fail_start(_thread):
                raise RuntimeError("worker start unavailable")

            patched.setattr(threading.Thread, "start", fail_start)
            with pytest.raises(RuntimeError, match="worker start unavailable"):
                store.start_run([source["id"]], {})
        failed = store.list_runs()[0]
        assert failed["status"] == "failed"
        assert failed["progress"]["stage"] == "failed"
        retry = wait(store, store.retry_run(failed["id"])["id"])
        assert retry["status"] == "completed"


@pytest.mark.parametrize("action", ["start", "retry"])
def test_existing_observer_recovers_worker_that_died_after_observer_opened(tmp_path, action):
    root = tmp_path / "state"
    with Store(root) as seed:
        source = dataset(seed)
    script = """
import sys, time
from pathlib import Path
from data_curator import store as module
from data_curator.store import Store
def blocked(*args, **kwargs):
    Path(sys.argv[3]).write_text('ready')
    while True:
        time.sleep(0.1)
module.run_pipeline = blocked
store = Store(sys.argv[1])
store.start_run([sys.argv[2]], {})
while True:
    time.sleep(0.1)
"""
    ready = tmp_path / "worker-ready"
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(root), source["id"], str(ready)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "Child worker did not start within five seconds"
        with Store(root) as observer:
            interrupted = observer.list_runs()[0]
            assert interrupted["status"] == "running"
            process.terminate()
            process.wait(timeout=5)
            run = (
                observer.start_run([source["id"]], {})
                if action == "start"
                else observer.retry_run(interrupted["id"])
            )
            assert wait(observer, run["id"])["status"] == "completed"
            recovered = observer.get_run(interrupted["id"])
            assert recovered["status"] == "failed"
            assert recovered["progress"]["stage"] == "failed"
            assert "interrupted" in recovered["error"].lower()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def test_export_preflights_directory_collision_before_overwriting(tmp_path):
    with Store(tmp_path / "state") as store:
        source = dataset(store)
        run = wait(store, store.start_run([source["id"]], {})["id"])
        output = tmp_path / "out"
        output.mkdir()
        sentinel = b"Previous export must survive failed preflight.\n"
        (output / ARTIFACTS[0]).write_bytes(sentinel)
        (output / ARTIFACTS[-1]).mkdir()
        with pytest.raises(ValueError, match="regular file|directory"):
            _export(store, run["id"], output, True)
        assert (output / ARTIFACTS[0]).read_bytes() == sentinel
        assert not list(output.glob(".export-*"))


@pytest.mark.parametrize("header", ["Host", "Origin"])
@pytest.mark.parametrize("authority", ["@127.0.0.1:{port}", "local\thost:{port}"])
def test_ambiguous_authority_is_not_accepted(live_api, header, authority):
    value = authority.format(port=live_api.port)
    if header == "Origin":
        value = "http://" + value
    status, _, _ = live_api.request("/api/health", headers={header: value})
    assert status == 403


@pytest.mark.parametrize("header", ["Host", "Origin"])
def test_duplicate_authority_headers_are_rejected(live_api, header):
    connection = http.client.HTTPConnection("127.0.0.1", live_api.port, timeout=3)
    try:
        connection.putrequest("GET", "/api/health", skip_host=True)
        connection.putheader("Host", f"127.0.0.1:{live_api.port}")
        if header == "Origin":
            connection.putheader("Origin", live_api.url)
        connection.putheader(header, "evil.example")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 403
        response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("target", ["/secret.txt", "/nested/secret.txt", "/fallback"])
def test_static_symlinks_cannot_escape_root(tmp_path, target):
    static = tmp_path / "public"
    static.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("OUTSIDE_ROOT_SECRET")
    (static / "secret.txt").symlink_to(outside / "secret.txt")
    (static / "nested").symlink_to(outside, target_is_directory=True)
    (static / "index.html").symlink_to(outside / "secret.txt")
    with running_api(tmp_path / "state", static) as api:
        status, content, _ = api.request(target)
        assert status == 404
        assert "OUTSIDE_ROOT_SECRET" not in str(content)


def test_custom_endpoint_is_pinned_for_omitted_recipe_endpoint(tmp_path):
    endpoint = "http://127.0.0.1:12345/v1"
    server = create_server(port=0, data_dir=tmp_path / "state", model_endpoint=endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        from test_integration import LocalAPI

        api = LocalAPI(server)
        source = api.upload(
            "Offline", [{"text": "Generation remains disabled in this endpoint pinning test."}]
        )
        assert api.ok("/api/recipe/default")["create"]["endpoint"] == endpoint
        saved = api.ok("/api/recipes", "POST", {"name": "Pinned", "recipe": {}})
        assert saved["recipe"]["create"]["endpoint"] == endpoint
        run = api.ok("/api/runs", "POST", {"dataset_ids": [source["id"]]})
        assert api.wait(run["id"])["recipe"]["create"]["endpoint"] == endpoint
        status, _, _ = api.request(
            "/api/runs",
            "POST",
            {
                "dataset_ids": [source["id"]],
                "recipe": {"create": {"endpoint": "http://127.0.0.1:54321/v1"}},
            },
        )
        assert status == 400
        assert len(api.ok("/api/runs")["runs"]) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("name", sorted(Store.ARTIFACTS))
def test_legacy_artifacts_are_verified_without_new_integrity_metadata(tmp_path, name):
    with Store(tmp_path / "state") as store:
        source = dataset(store)
        run = wait(store, store.start_run([source["id"]], {})["id"])
        expected = store.get_artifact(run["id"], name)
        # Simulate a workspace completed by the previous version of the store.
        run.pop("artifact_integrity")
        with store._connect() as connection:
            connection.execute("UPDATE runs SET payload=? WHERE id=?", (json.dumps(run), run["id"]))
        assert store.get_artifact(run["id"], name) == expected
        (store.root / "runs" / run["id"] / name).write_bytes(b"corrupted\n")
        with pytest.raises(ConflictError, match="checksum|integrity"):
            store.get_artifact(run["id"], name)


def test_export_cannot_replace_another_runs_immutable_artifacts(tmp_path):
    with Store(tmp_path / "state") as store:
        source = dataset(store)
        first = wait(store, store.start_run([source["id"]], {"seed": 1})["id"])
        second = wait(store, store.start_run([source["id"]], {"seed": 2})["id"])
        before = {name: store.get_artifact(first["id"], name) for name in ARTIFACTS}
        with pytest.raises(ValueError, match="managed workspace"):
            _export(store, second["id"], store.root / "runs" / first["id"], True)
        assert {name: store.get_artifact(first["id"], name) for name in ARTIFACTS} == before


def test_no_overwrite_export_cannot_clobber_a_concurrently_created_file(tmp_path, monkeypatch):
    import data_curator.cli as cli

    with Store(tmp_path / "state") as store:
        source = dataset(store)
        run = wait(store, store.start_run([source["id"]], {})["id"])
        output = tmp_path / "out"
        actual = cli.tempfile.NamedTemporaryFile
        sentinel = b"Created concurrently after export preflight.\n"

        def concurrent_file(**kwargs):
            (output / ARTIFACTS[0]).write_bytes(sentinel)
            return actual(**kwargs)

        monkeypatch.setattr(cli.tempfile, "NamedTemporaryFile", concurrent_file)
        with pytest.raises(FileExistsError):
            _export(store, run["id"], output, False)
        assert (output / ARTIFACTS[0]).read_bytes() == sentinel
        assert not list(output.glob(".export-*"))


def test_shutdown_racing_start_cancels_the_new_worker(tmp_path, monkeypatch):
    from data_curator.pipeline import CancelledError

    with Store(tmp_path / "state") as store:
        source = dataset(store)
        entered = threading.Event()
        release = threading.Event()
        close_requested = threading.Event()
        observed_cancel = threading.Event()
        actual = store._dataset_metadata

        def paused_metadata(identifier):
            entered.set()
            assert release.wait(3)
            return actual(identifier)

        def paused_pipeline(_datasets, _recipe, *, cancel, progress):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if cancel():
                    observed_cancel.set()
                    raise CancelledError("shutdown")
                time.sleep(0.005)
            raise AssertionError("Shutdown missed the new worker")

        monkeypatch.setattr(store, "_dataset_metadata", paused_metadata)
        monkeypatch.setattr(store_module, "run_pipeline", paused_pipeline)
        failures = []

        def start():
            try:
                store.start_run([source["id"]], {})
            except BaseException as exc:
                failures.append(exc)

        def close():
            close_requested.set()
            store.close()

        starter = threading.Thread(target=start)
        closer = threading.Thread(target=close)
        starter.start()
        assert entered.wait(2)
        closer.start()
        assert close_requested.wait(2)
        release.set()
        starter.join(5)
        closer.join(5)
        assert not starter.is_alive() and not closer.is_alive()
        assert not failures
        run = store.list_runs()[0]
        assert run["status"] == "cancelled"
        assert observed_cancel.is_set() or run["progress"]["stage"] == "cancelled"


def test_concurrent_stores_admit_only_one_worker_and_cancel_is_idempotent(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from data_curator.pipeline import CancelledError

    def blocked(_datasets, _recipe, *, cancel, progress):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if cancel():
                raise CancelledError("cancelled")
            time.sleep(0.005)
        raise AssertionError("Test did not cancel the admitted worker")

    monkeypatch.setattr(store_module, "run_pipeline", blocked)
    with Store(tmp_path / "state") as first, Store(tmp_path / "state") as second:
        source = dataset(first)
        barrier = threading.Barrier(2)

        def start(store):
            barrier.wait(timeout=3)
            try:
                return store, store.start_run([source["id"]], {})
            except ConflictError:
                return store, None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(start, (first, second)))
        admitted = [(store, run) for store, run in results if run is not None]
        assert len(admitted) == 1
        owner, run = admitted[0]
        observer = second if owner is first else first
        with pytest.raises(ConflictError, match="another worker"):
            observer.cancel_run(run["id"])
        owner.cancel_run(run["id"])
        assert wait(owner, run["id"])["status"] == "cancelled"
        assert owner.cancel_run(run["id"])["status"] == "cancelled"
        assert len(owner.list_runs()) == 1


def test_suspicious_nonregular_dataset_file_fails_without_blocking(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO regression is POSIX-only")
    with Store(tmp_path / "state") as store:
        source = dataset(store)
        path = store.root / "datasets" / source["id"] / "records.jsonl"
        path.unlink()
        os.mkfifo(path)
        with pytest.raises(ConflictError, match="integrity"):
            store.get_dataset(source["id"])


def test_cli_corrupt_workspace_is_a_json_error_without_traceback(tmp_path, capsys):
    from data_curator.cli import main

    state = tmp_path / "state"
    state.mkdir()
    (state / "curator.sqlite3").write_bytes(b"broken sqlite workspace")
    assert main(["--data-dir", str(state), "datasets"]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "database" in json.loads(captured.err)["error"].lower()
    assert "Traceback" not in captured.err
