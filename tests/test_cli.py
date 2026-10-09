from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from data_curator.cli import ARTIFACTS, main
from data_curator.demo import demo_datasets
from data_curator.store import Store


def invoke(capsys, state: Path, *arguments: str):
    code = main(["--data-dir", str(state), *arguments])
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if captured.out.strip() else None, captured.err


def test_cli_demo_repeat_run_and_bundle_export(tmp_path, capsys):
    state = tmp_path / "state"
    code, demo, error = invoke(capsys, state, "demo")
    assert code == 0, error
    assert len(demo["datasets"]) == 5
    assert {d["role"] for d in demo["datasets"]} == {"source", "target", "holdout"}
    code, repeated, error = invoke(capsys, state, "demo")
    assert code == 0, error
    assert [d["id"] for d in repeated["datasets"]] == [d["id"] for d in demo["datasets"]]
    code, first, error = invoke(capsys, state, "run", "--seed", "19", "--max-records", "7")
    assert code == 0, error
    assert first["status"] == "completed"
    assert first["summary"]["output_records"] == 7
    code, second, error = invoke(capsys, state, "run", "--seed", "19", "--max-records", "7")
    assert code == 0, error
    assert first["manifest"]["output_sha256"] == second["manifest"]["output_sha256"]
    directory = tmp_path / "bundle"
    code, exported, error = invoke(capsys, state, "export", first["id"], "--output", str(directory))
    assert code == 0, error
    assert {file["name"] for file in exported["files"]} == set(ARTIFACTS)
    for file in exported["files"]:
        assert hashlib.sha256((directory / file["name"]).read_bytes()).hexdigest() == file["sha256"]
    assert (
        hashlib.sha256((directory / "curated.jsonl").read_bytes()).hexdigest()
        == first["manifest"]["output_sha256"]
    )
    assert json.loads((directory / "recipe.json").read_text())["seed"] == 19
    rejected = [json.loads(line) for line in (directory / "rejected.jsonl").read_text().splitlines()]
    assert rejected
    code, _, error = invoke(capsys, state, "export", first["id"], "--output", str(directory))
    assert code == 1
    assert "would replace" in error
    code, _, error = invoke(capsys, state, "export", first["id"], "--output", str(directory), "--overwrite")
    assert code == 0, error
    code, runs, error = invoke(capsys, state, "runs")
    assert code == 0, error
    assert len(runs["runs"]) == 2


def test_cli_import_immutable_code_and_license(tmp_path, capsys):
    content = b"def Water():\n    return 'WET'\n"
    path = tmp_path / "water.py"
    path.write_bytes(content)
    code, dataset, error = invoke(
        capsys,
        tmp_path / "state",
        "import",
        str(path),
        "--name",
        "Sensor code",
        "--kind",
        "code",
        "--license",
        "MIT",
    )
    assert code == 0, error
    assert dataset["sha256"] == hashlib.sha256(content).hexdigest()
    with Store(tmp_path / "state") as store:
        stored = store.get_dataset(dataset["id"])
        assert stored["records"][0]["text"] == content.decode()
        assert stored["records"][0]["kind"] == "code"
        assert stored["declared_license"] == "MIT"
    path.write_text("modified after import")
    with Store(tmp_path / "state") as store:
        assert store.get_dataset(dataset["id"])["records"][0]["text"] == content.decode()


@pytest.mark.parametrize(
    ("filename", "content", "error_fragment"),
    [
        ("bad.jsonl", b'{"text": }', "JSON"),
        ("bad.jsonl", b'{"text": 123}', "text"),
        ("empty.jsonl", b"", "empty"),
        ("bad.jsonl", b"\xff\xfe", "UTF"),
    ],
)
def test_invalid_import_is_atomic(tmp_path, capsys, filename, content, error_fragment):
    source = tmp_path / filename
    source.write_bytes(content)
    code, result, error = invoke(capsys, tmp_path / "state", "import", str(source))
    assert code == 1
    assert result is None
    assert error_fragment.lower() in error.lower()
    code, datasets, error = invoke(capsys, tmp_path / "state", "datasets")
    assert code == 0, error
    assert datasets == {"datasets": []}


def test_import_size_checked_before_ingest(tmp_path, capsys):
    source = tmp_path / "large.txt"
    with source.open("wb") as handle:
        handle.truncate(20 * 1024 * 1024 + 1)
    code, result, error = invoke(capsys, tmp_path / "state", "import", str(source))
    assert code == 1
    assert "20 MiB" in error
    assert result is None


@pytest.mark.parametrize(
    "content", ["[]", '{"compose":{"weights":{"source":-1}}}', '{"seed":NaN}', '{"unknown":1}']
)
def test_invalid_recipe_is_clear_without_starting_run(tmp_path, capsys, content):
    recipe = tmp_path / "recipe.json"
    recipe.write_text(content)
    code, result, error = invoke(capsys, tmp_path / "state", "run", "--recipe", str(recipe))
    assert code == 1
    assert result is None
    assert "error" in json.loads(error)
    code, runs, error = invoke(capsys, tmp_path / "state", "runs")
    assert code == 0, error
    assert runs == {"runs": []}


def test_failed_generation_has_nonzero_exit_without_fake_output(tmp_path, capsys, monkeypatch):
    from data_curator import generation

    invoke(capsys, tmp_path / "state", "demo")
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({"create": {"enabled": True, "model": "offline-fixture"}}))

    def unavailable(*args, **kwargs):
        raise generation.GenerationError("Fixture local model unavailable")

    monkeypatch.setattr(generation, "generate_records", unavailable)
    code, run, _ = invoke(capsys, tmp_path / "state", "run", "--recipe", str(recipe))
    assert code == 1
    assert run["status"] == "failed"
    assert "Fixture local model unavailable" in run["error"]
    assert not (tmp_path / "state" / "runs" / run["id"] / "curated.jsonl").exists()


def test_data_dir_after_command_and_python_module_entrypoint(tmp_path, capsys):
    assert main(["datasets", "--data-dir", str(tmp_path / "after")]) == 0
    assert json.loads(capsys.readouterr().out) == {"datasets": []}
    completed = subprocess.run(
        [sys.executable, "-m", "data_curator", "recipe"],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert json.loads(completed.stdout)["create"]["enabled"] is False


def test_demo_fixtures_are_fresh_original_and_role_separated():
    first = demo_datasets()
    first[0]["name"] = "changed"
    fixtures = demo_datasets()
    assert fixtures[0]["name"] == "Garden knowledge"
    assert all(dataset["license"] == "MIT" for dataset in fixtures)
    target = next(d for d in fixtures if d["role"] == "target")
    holdout = next(d for d in fixtures if d["role"] == "holdout")
    secret = json.loads(holdout["content"])["text"]
    assert secret not in target["content"]
    assert any(secret in source["content"] for source in fixtures if source["role"] == "source")
