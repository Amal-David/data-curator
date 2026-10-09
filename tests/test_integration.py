"""Exercise the shared engine through a real loopback HTTP server and SQLite store."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from data_curator.server import create_server


class LocalAPI:
    def __init__(self, server):
        self.server = server
        self.port = server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path, method="GET", body=None, *, raw_body=None, headers=None):
        payload = raw_body
        if method == "POST" and body is None and raw_body is None:
            body = {}
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.url + path,
            data=payload,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            response = self.opener.open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            parsed = (
                json.loads(content)
                if "application/json" in response.headers.get("Content-Type", "")
                else content
            )
            return response.status, parsed, response.headers

    def ok(self, path, method="GET", body=None, *, expected=(200, 201, 202)):
        status, value, _ = self.request(path, method, body)
        assert status in expected, (status, value)
        return value

    def upload(self, name, records, role="source", filename="records.jsonl"):
        return self.ok(
            "/api/datasets",
            "POST",
            {
                "name": name,
                "role": role,
                "filename": filename,
                "content": "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
            },
        )

    def wait(self, run_id, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.ok(f"/api/runs/{run_id}")
            if run["status"] in {"completed", "cancelled", "failed"}:
                return run
            time.sleep(0.01)
        raise AssertionError(f"Run {run_id} did not reach a terminal state")


@contextmanager
def running_api(data_dir: Path, static_dir: Path | None = None):
    server = create_server(host="127.0.0.1", port=0, data_dir=data_dir, static_dir=static_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield LocalAPI(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.fixture
def live_api(tmp_path):
    with running_api(tmp_path / "state") as api:
        yield api


def permissive_recipe(api):
    recipe = api.ok("/api/recipe/default")
    recipe["clean"].update(min_chars=1, dedup_threshold=0.9, holdout_ngram=5)
    recipe["calibrate"].update(quality_min=0, relevance_min=0)
    recipe["compose"]["max_records"] = 100
    return recipe


def plant_workflow(api):
    original = "A database transaction commits all related changes atomically and preserves durable records."
    holdout = "amber herons inspect violet pebbles beside crystalline rivers"
    source_a = api.upload(
        "Engineering notes",
        [
            {"text": original, "metadata": {"license": "MIT", "topic": "databases"}},
            {"text": "def greet(User):\n    return f'Hello, {User}!'\n", "kind": "code"},
            {"text": f"The evaluation answer contains {holdout} while the remaining sentence supplies context."},
        ],
    )
    source_b = api.upload(
        "Multilingual notes",
        [
            {"text": original},
            {"text": "数据库事务保证操作的原子性、一致性、隔离性和持久性。数学公式 ∑ᵢ xᵢ² ≥ 0 表达非负平方和。"},
        ],
    )
    target = api.upload("Calibration examples", [{"text": "Explain reliable database transactions and Python functions."}], "target")
    evaluation = api.upload("Evaluation reference", [{"text": holdout}], "holdout")
    return [source_a, source_b, target, evaluation]


def test_full_workflow_reconciles_export_roles_decisions_and_repeat_hashes(live_api):
    datasets = plant_workflow(live_api)
    recipe = permissive_recipe(live_api)
    payload = {"dataset_ids": [d["id"] for d in datasets], "recipe": recipe}
    first = live_api.ok("/api/runs", "POST", payload)
    complete = live_api.wait(first["id"])
    assert complete["status"] == "completed", complete
    records = live_api.ok(f"/api/runs/{first['id']}/records")
    assert records["total"] == 3
    assert len({r["id"] for r in records["records"]}) == records["total"]
    assert {r["dataset_id"] for r in records["records"]} <= {d["id"] for d in datasets[:2]}
    assert any("数据库" in r["text"] for r in records["records"])
    assert any("\n    return" in r["text"] for r in records["records"])
    rejected = live_api.ok(f"/api/runs/{first['id']}/decisions?action=reject")
    assert rejected["total"] == 2
    assert all(d["action"] == "reject" for d in rejected["decisions"])
    reasons = " ".join(d["reason"] for d in rejected["decisions"]).lower()
    assert "duplicate" in reasons
    assert "holdout" in reasons or "contamination" in reasons
    status, exported, headers = live_api.request(f"/api/runs/{first['id']}/export")
    assert status == 200
    assert "attachment" in headers["Content-Disposition"]
    assert [json.loads(line) for line in exported.splitlines()] == records["records"]
    manifest = live_api.ok(f"/api/runs/{first['id']}/manifest")
    assert manifest["output_sha256"] == hashlib.sha256(exported).hexdigest()
    _, rejected_export, _ = live_api.request(f"/api/runs/{first['id']}/rejected")
    assert manifest["rejected_sha256"] == hashlib.sha256(rejected_export).hexdigest()
    assert len(rejected_export.splitlines()) == rejected["total"]
    assert live_api.ok(f"/api/runs/{first['id']}/report") == complete["summary"]
    saved_recipe = live_api.ok(f"/api/runs/{first['id']}/recipe")
    canonical_recipe = json.dumps(saved_recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert manifest["recipe_sha256"] == hashlib.sha256(canonical_recipe).hexdigest()
    assert complete["summary"]["input_records"] == 5
    assert complete["summary"]["output_records"] == 3
    assert complete["summary"]["synthetic_records"] == 0
    assert complete["summary"]["rejected_records"] + complete["summary"]["output_records"] == 5
    assert "create" in json.dumps(complete["summary"]["skipped_stages"])
    assert "estimate" in manifest["token_count_method"]
    second = live_api.ok("/api/runs", "POST", payload)
    assert second["id"] != first["id"]
    assert live_api.wait(second["id"])["status"] == "completed"
    assert live_api.request(f"/api/runs/{second['id']}/export")[1] == exported
    assert live_api.ok(f"/api/runs/{second['id']}/manifest") == manifest


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("bad.jsonl", '{"text":"valid first row"}\n{"text":'),
        ("bad.jsonl", '{"text":"valid first row"}\n{"text":42}'),
        ("bad.jsonl", '{"text":"valid first row"}\n{"text":"bad metadata", "metadata":[]}'),
        ("empty.jsonl", ""),
        ("bad.csv", "other\nvalue"),
        ("bad.json", '{"text":"root must be an array"}'),
    ],
)
def test_invalid_import_is_atomic_and_subsequent_valid_import_succeeds(live_api, filename, content):
    before = live_api.ok("/api/datasets")
    status, error, _ = live_api.request(
        "/api/datasets", "POST", {"name": "Broken input", "role": "source", "filename": filename, "content": content}
    )
    assert status == 400, error
    assert error["error"]
    assert live_api.ok("/api/datasets") == before
    good = live_api.upload("Recovery", [{"text": "This valid record demonstrates that a failed import does not poison the database."}])
    assert good["record_count"] == 1


def test_demo_and_saved_recipe_are_reusable_and_persist_across_restart(tmp_path):
    data_dir = tmp_path / "persistent-state"
    with running_api(data_dir) as api:
        first_demo = api.ok("/api/demo", "POST")["datasets"]
        second_demo = api.ok("/api/demo", "POST")["datasets"]
        assert {d["id"] for d in first_demo} == {d["id"] for d in second_demo}
        assert len(api.ok("/api/datasets")["datasets"]) == len(first_demo)
        recipe = permissive_recipe(api)
        saved = api.ok("/api/recipes", "POST", {"name": "Reproducible demo", "recipe": recipe})
        run = api.ok("/api/runs", "POST", {"dataset_ids": [d["id"] for d in first_demo], "recipe": recipe})
        assert api.wait(run["id"])["status"] == "completed"
        output = api.request(f"/api/runs/{run['id']}/export")[1]
        manifest = api.ok(f"/api/runs/{run['id']}/manifest")
    with running_api(data_dir) as api:
        assert {d["id"] for d in api.ok("/api/datasets")["datasets"]} == {d["id"] for d in first_demo}
        assert any(r["id"] == saved["id"] for r in api.ok("/api/recipes")["recipes"])
        assert api.ok(f"/api/runs/{run['id']}")["status"] == "completed"
        assert api.request(f"/api/runs/{run['id']}/export")[1] == output
        assert api.ok(f"/api/runs/{run['id']}/manifest") == manifest


def test_cancellation_is_terminal_and_retry_runs_same_inputs(live_api, monkeypatch):
    import data_curator.store as store_module
    from data_curator.pipeline import CancelledError

    original_pipeline = store_module.run_pipeline
    started = threading.Event()

    def cancellable_pipeline(datasets, recipe, *, cancel=None, progress=None):
        started.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if cancel and cancel():
                raise CancelledError("Cancelled by integration test")
            time.sleep(0.005)
        raise AssertionError("The worker did not receive cancellation")

    monkeypatch.setattr(store_module, "run_pipeline", cancellable_pipeline)
    dataset = live_api.upload("Cancel me", [{"text": "A cancellation must leave original source records unchanged and allow a reproducible retry."}])
    payload = {"dataset_ids": [dataset["id"]], "recipe": permissive_recipe(live_api)}
    run = live_api.ok("/api/runs", "POST", payload)
    assert started.wait(2)
    assert live_api.request("/api/runs", "POST", payload)[0] == 409
    live_api.ok(f"/api/runs/{run['id']}/cancel", "POST")
    assert live_api.wait(run["id"])["status"] == "cancelled"
    assert live_api.ok(f"/api/datasets/{dataset['id']}")["record_count"] == 1
    monkeypatch.setattr(store_module, "run_pipeline", original_pipeline)
    retry = live_api.ok(f"/api/runs/{run['id']}/retry", "POST")
    assert retry["id"] != run["id"]
    assert retry["dataset_ids"] == run["dataset_ids"]
    assert retry["recipe"] == run["recipe"]
    assert live_api.wait(retry["id"])["status"] == "completed"
    assert live_api.ok(f"/api/runs/{retry['id']}/records")["total"] == 1
    repeated = live_api.ok("/api/runs", "POST", payload)
    assert live_api.wait(repeated["id"])["status"] == "completed"
    assert live_api.request(f"/api/runs/{repeated['id']}/export")[1] == live_api.request(f"/api/runs/{retry['id']}/export")[1]


def test_all_rejected_run_has_valid_empty_export(live_api):
    dataset = live_api.upload("Too short", [{"text": "tiny"}])
    recipe = permissive_recipe(live_api)
    recipe["clean"]["min_chars"] = 100
    run = live_api.ok("/api/runs", "POST", {"dataset_ids": [dataset["id"]], "recipe": recipe})
    assert live_api.wait(run["id"])["status"] == "completed"
    assert live_api.ok(f"/api/runs/{run['id']}/records")["total"] == 0
    status, exported, _ = live_api.request(f"/api/runs/{run['id']}/export")
    assert status == 200 and exported == b""
    assert live_api.ok(f"/api/runs/{run['id']}/manifest")["output_sha256"] == hashlib.sha256(b"").hexdigest()


def test_dataset_referenced_by_run_cannot_be_deleted(live_api):
    dataset = live_api.upload("Immutable source", [{"text": "Run lineage must remain inspectable even after a completed curation job."}])
    run = live_api.ok("/api/runs", "POST", {"dataset_ids": [dataset["id"]], "recipe": permissive_recipe(live_api)})
    assert live_api.wait(run["id"])["status"] == "completed"
    assert live_api.request(f"/api/datasets/{dataset['id']}", "DELETE")[0] == 409
    assert live_api.ok(f"/api/datasets/{dataset['id']}")["id"] == dataset["id"]


def test_impossible_budget_reports_actual_unique_output_without_replacement(live_api):
    dataset = live_api.upload("Limited supply", [{"text": "Only one source document is available for this deliberately oversized mixture request."}])
    recipe = permissive_recipe(live_api)
    recipe["compose"]["max_records"] = 10
    run = live_api.ok("/api/runs", "POST", {"dataset_ids": [dataset["id"]], "recipe": recipe})
    done = live_api.wait(run["id"])
    assert done["status"] == "completed"
    output = live_api.ok(f"/api/runs/{run['id']}/records")
    assert output["total"] == 1
    assert done["summary"]["output_records"] == 1
    # The public manifest/report must make the shortage observable, not silently pad.
    report = json.dumps({"summary": done["summary"], "manifest": done["manifest"]}).lower()
    assert "shortage" in report or "shortfall" in report
