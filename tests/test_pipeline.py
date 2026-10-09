"""Behavioral tests of the shared deterministic curation engine."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys

import pytest

from data_curator import generation
from data_curator.pipeline import CancelledError, default_recipe, export_jsonl, run_pipeline, validate_recipe


def dataset(identifier, texts, role="source", kind="text"):
    return {
        "id": identifier,
        "name": identifier,
        "role": role,
        "sha256": "upload-test-hash",
        "license": "MIT",
        "records": [
            {"id": f"row-{index}", "text": text, "kind": kind, "metadata": {"original": True}}
            for index, text in enumerate(texts)
        ],
    }


def permissive(**changes):
    recipe = default_recipe()
    recipe["clean"].update(min_chars=1, dedup_threshold=1.0)
    recipe["calibrate"].update(quality_min=0, relevance_min=0)
    for key, value in changes.items():
        if isinstance(value, dict):
            recipe[key].update(value)
        else:
            recipe[key] = value
    return recipe


def reasons(result):
    return [item["reason"] for item in result["decisions"] if item["action"] == "reject"]


def test_cross_source_exact_duplicates_and_explicit_lineage():
    text = "Solar observatories measure magnetic fields above the photosphere."
    sources = [
        dataset("alpha", [text]),
        dataset("beta", [text, "Coral reefs support complex marine food webs."]),
    ]
    original = copy.deepcopy(sources)
    result = run_pipeline(sources, permissive())
    assert sources == original  # Inputs remain immutable.
    assert len(result["records"]) == 2
    assert reasons(result) == ["exact_duplicate"]
    rejected = result["rejected_records"][0]
    assert rejected["rejection"]["details"]["duplicate_of"] == "alpha:row-0"
    assert all(record["metadata"]["license"] == "MIT" for record in result["records"])
    assert all(record["lineage"]["origin"] == "original" for record in result["records"])
    assert result["summary"]["input_records"] == 3
    assert result["summary"]["rejected_records"] == 1


def test_near_duplicate_verified_and_below_threshold_retained():
    shared = "A careful gardener measures soil moisture before watering the young plants each morning"
    texts = [
        shared + " near the sheltered greenhouse entrance.",
        shared + " near the sheltered greenhouse doorway.",
        "A careful gardener harvests ripe tomatoes and carries a basket toward the market stall.",
    ]
    result = run_pipeline([dataset("garden", texts)], permissive(clean={"dedup_threshold": 0.75}))
    assert len(result["records"]) == 2
    near = [decision for decision in result["decisions"] if decision["reason"] == "near_duplicate"]
    assert len(near) == 1
    assert 0.75 <= near[0]["details"]["jaccard"] < 1
    assert texts[2] in [record["text"] for record in result["records"]]


def test_lsh_candidate_still_requires_actual_jaccard(monkeypatch):
    from data_curator.pipeline import _NearIndex

    index = _NearIndex(0.9)
    monkeypatch.setattr(index, "_signature", lambda shingles, cancel: tuple([1] * 32))
    for number in range(520):
        # Force LSH collisions while all actual Jaccard values are below threshold.
        assert (
            index.check_and_add(
                {"id": str(number), "kind": "text", "text": f"Unique{number} token{number} word{number}"},
                None,
            )
            is None
        )
    assert len(index.entries) == 520


def test_code_indentation_case_order_and_original_bytes_preserved():
    texts = [
        "if active:\n    emit(Value)\nfinish()\n",
        "if active:\n    emit(Value)\n    finish()\n",
        "if active:\n    emit(value)\nfinish()\n",
        "finish()\nif active:\n    emit(Value)\n",
    ]
    result = run_pipeline(
        [dataset("code", texts + [texts[0]], kind="code")], permissive(clean={"dedup_threshold": 0.01})
    )
    assert {record["text"] for record in result["records"]} == set(texts)
    assert len(result["records"]) == 4
    assert reasons(result) == ["exact_duplicate"]
    assert "text-only" in result["manifest"]["algorithms"]["near_dedup"]


def test_unicode_math_and_code_pass_default_quality_heuristics():
    texts = [
        "研究者使用公开的数据集评估语言模型，并且记录每个实验的参数和可重复运行的结果。",
        "∀ x ∈ ℝ, f(x) = x² + 2x + 1 = (x + 1)². ∫₀¹ x² dx = ⅓.",
        "البيانات المحلية تساعد الباحثين على مراجعة الجودة مع الحفاظ على الخصوصية.",
    ]
    result = run_pipeline([dataset("unicode", texts)], default_recipe())
    assert len(result["records"]) == 3
    assert all(record["quality_score"] >= 0.35 for record in result["records"])
    assert all(record["text"] in texts for record in result["records"])


def test_low_information_text_rejected_by_quality():
    result = run_pipeline([dataset("junk", ["!" * 80, "spam " * 60])], default_recipe())
    assert result["records"] == []
    assert reasons(result) == ["low_quality", "low_quality"]


def test_embedded_holdout_excluded_and_target_is_not_holdout():
    heldout = "The secret evaluation phrase contains eight distinct reserved lexical units."
    target = "Calibration examples describe reliable testing and transparent reporting."
    result = run_pipeline(
        [
            dataset(
                "source",
                [
                    "Additional context. " + heldout + " More material follows.",
                    target,
                    "Ocean tides rise under lunar gravity.",
                ],
            ),
            dataset("target", [target], "target"),
            dataset("holdout", [heldout], "holdout"),
        ],
        permissive(),
    )
    assert len(result["records"]) == 2
    assert target in [record["text"] for record in result["records"]]
    assert reasons(result) == ["holdout_overlap"]
    assert result["summary"]["input_records"] == 3
    assert heldout not in json.dumps(result["decisions"], ensure_ascii=False)


def test_cjk_holdout_overlap_without_whitespace():
    reserved = "这个测试案例只能用于评估模型不可出现在训练数据之中"
    result = run_pipeline(
        [
            dataset("source", ["不同的开头" + reserved + "还有其他内容"]),
            dataset("holdout", [reserved], "holdout"),
        ],
        permissive(),
    )
    assert result["records"] == []
    assert reasons(result) == ["holdout_overlap"]


def test_holdout_never_affects_relevance_fit():
    source = dataset(
        "source",
        [
            "Orchids need gentle sunlight and stable humidity to bloom.",
            "Databases store records using indexed keys and transaction logs.",
        ],
    )
    target = dataset("target", ["Gardening orchids sunlight humidity"], "target")
    first = run_pipeline([source, target], permissive())
    second = run_pipeline(
        [source, target, dataset("holdout", ["orchids unrelated obscure labels"], "holdout")], permissive()
    )
    assert [(r["id"], r["relevance_score"]) for r in first["records"]] == [
        (r["id"], r["relevance_score"]) for r in second["records"]
    ]
    assert first["records"][0]["quality_score"] == second["records"][0]["quality_score"]


def test_relevance_threshold_prefers_target_domain():
    result = run_pipeline(
        [
            dataset(
                "source",
                [
                    "Orchids sunlight humidity gardening flowers",
                    "Binary search sorted array midpoint algorithm",
                ],
            ),
            dataset("target", ["Orchids sunlight humidity gardening flowers"], "target"),
        ],
        permissive(calibrate={"relevance_min": 0.5}),
    )
    assert len(result["records"]) == 1
    assert "Orchids" in result["records"][0]["text"]
    assert reasons(result) == ["low_relevance"]


def test_contaminated_source_never_sent_to_generator_and_outputs_rescreened(monkeypatch):
    heldout = "Reserved secret phrase with enough words for isolated benchmark protection."
    valid = "A wind turbine converts rotating mechanical energy into electric power."
    received = []

    def fake_generate(records, config, *, cancel=None):
        received.extend(records)
        parent = records[0]
        output = {
            **parent,
            "id": "syn-1",
            "text": heldout,
            "lineage": {
                "origin": "synthetic",
                "parent_ids": [parent["id"]],
                "model": "test-model",
                "prompt_sha256": "test-hash",
            },
        }
        return [output], []

    monkeypatch.setattr(generation, "generate_records", fake_generate)
    result = run_pipeline(
        [dataset("source", [heldout, valid]), dataset("holdout", [heldout], "holdout")],
        permissive(create={"enabled": True, "model": "test-model"}),
    )
    assert [record["text"] for record in received] == [valid]
    assert len(result["records"]) == 1
    assert reasons(result).count("holdout_overlap") == 2
    assert result["summary"]["synthetic_records"] == 0
    assert len(result["generated_records"]) == 1
    assert (
        result["manifest"]["generated_candidates_sha256"]
        == hashlib.sha256(export_jsonl(result["generated_records"])).hexdigest()
    )


def test_generated_duplicates_and_low_quality_are_rescreened(monkeypatch):
    original = "Photovoltaic panels convert sunlight into electrical current using semiconductor junctions."

    def fake_generate(records, config, *, cancel=None):
        parent = records[0]
        return [
            {
                **parent,
                "id": f"synthetic-{i}",
                "text": text,
                "lineage": {"origin": "synthetic", "parent_ids": [parent["id"]]},
            }
            for i, text in enumerate([original, "!" * 80])
        ], []

    monkeypatch.setattr(generation, "generate_records", fake_generate)
    result = run_pipeline(
        [dataset("source", [original])],
        permissive(calibrate={"quality_min": 0.35}, create={"enabled": True, "model": "test"}),
    )
    assert reasons(result) == ["exact_duplicate", "low_quality"]
    assert len(result["generated_records"]) == 2
    assert len(result["records"]) == 1


def test_generation_disabled_never_imports_or_calls_network(monkeypatch):
    monkeypatch.setattr(
        generation, "generate_records", lambda *args, **kwargs: pytest.fail("Must not call generator")
    )
    result = run_pipeline(
        [dataset("source", ["An original safe local document with sufficient length."])], default_recipe()
    )
    assert result["summary"]["skipped_stages"][0]["stage"] == "create"
    assert "disabled" in result["summary"]["skipped_stages"][0]["reason"].lower()
    assert result["generated_records"] == []


def test_generation_failure_is_explicit_without_fake_success(monkeypatch):
    def fail(*args, **kwargs):
        raise generation.GenerationError("Connection failed")

    monkeypatch.setattr(generation, "generate_records", fail)
    with pytest.raises(generation.GenerationError, match="Connection failed"):
        run_pipeline(
            [dataset("source", ["A source text for a failed local inference attempt."])],
            permissive(create={"enabled": True, "model": "test"}),
        )


def test_generation_cancellation_translated(monkeypatch):
    def stop(*args, **kwargs):
        raise generation.GenerationCancelled("cancelled while waiting")

    monkeypatch.setattr(generation, "generate_records", stop)
    with pytest.raises(CancelledError, match="cancelled"):
        run_pipeline(
            [dataset("source", ["A source text for cancelling a local inference request."])],
            permissive(create={"enabled": True, "model": "test"}),
        )


def test_generated_records_remain_replayable_if_budget_excludes_them(monkeypatch):
    def fake_generate(records, config, *, cancel=None):
        parent = records[0]
        return [
            {
                **parent,
                "id": "synthetic-replay",
                "text": "Distinct generated example about particle trajectories and detector calibration.",
                "lineage": {"origin": "synthetic", "parent_ids": [parent["id"]], "model": "test"},
            }
        ], []

    monkeypatch.setattr(generation, "generate_records", fake_generate)
    result = run_pipeline(
        [dataset("source", ["A lighthouse warns approaching vessels of rocky coastal hazards."])],
        permissive(create={"enabled": True, "model": "test"}, compose={"max_records": 1}),
    )
    assert len(result["records"]) == 1
    assert len(result["generated_records"]) == 1
    assert result["summary"]["synthetic_records"] == 1
    assert len(result["records"]) + len(result["rejected_records"]) == 2


def test_weighted_no_replacement_budget_and_shortages():
    sources = [
        dataset("a", [f"Code specimen Alpha_{i} = {i}\n" for i in range(20)], kind="code"),
        dataset("b", [f"Code specimen Beta_{i} = {i}\n" for i in range(20)], kind="code"),
    ]
    result = run_pipeline(sources, permissive(compose={"max_records": 12, "weights": {"a": 3, "b": 1}}))
    assert len(result["records"]) == 12
    assert len({record["id"] for record in result["records"]}) == 12
    assert [item["realized_records"] for item in result["summary"]["mixture"]] == [9, 3]
    assert result["summary"]["shortage_records"] == 0
    limited = run_pipeline(
        [sources[0], dataset("b", ["one tiny code example = 1"], kind="code")],
        permissive(compose={"max_records": 12, "weights": {"a": 1, "b": 1}}),
    )
    assert [item["realized_records"] for item in limited["summary"]["mixture"]] == [11, 1]
    assert limited["summary"]["mixture"][1]["source_shortage"] == 5
    impossible = run_pipeline(sources, permissive(compose={"max_records": 100, "weights": {"a": 1, "b": 0}}))
    assert len(impossible["records"]) == 20
    assert impossible["summary"]["shortage_records"] == 80
    assert "zero_source_weight" in reasons(impossible)


def test_deterministic_bytes_input_order_independent_and_seed_changes_sample():
    a = dataset("a", [f"value_{i} = {i} * 100" for i in range(20)], kind="code")
    b = dataset("b", [f"result_{i} = {i} + 15" for i in range(20)], kind="code")
    recipe = permissive(compose={"max_records": 10})
    first = run_pipeline([a, b], recipe)
    second = run_pipeline([b, a], recipe)
    assert first == second
    assert export_jsonl(first["records"]) == export_jsonl(second["records"])
    assert first["manifest"]["output_sha256"] == hashlib.sha256(export_jsonl(first["records"])).hexdigest()
    assert (
        first["manifest"]["rejected_sha256"]
        == hashlib.sha256(export_jsonl(first["rejected_records"])).hexdigest()
    )
    assert (
        first["manifest"]["decisions_sha256"] == hashlib.sha256(export_jsonl(first["decisions"])).hexdigest()
    )
    recipe["seed"] += 1
    third = run_pipeline([a, b], recipe)
    assert [record["id"] for record in first["records"]] != [record["id"] for record in third["records"]]


def test_cross_process_hash_seed_does_not_change_manifest(tmp_path):
    reserved = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
    inputs = [
        dataset("source", [reserved + " unique tail"]),
        dataset("h1", [reserved], "holdout"),
        dataset("h2", [reserved], "holdout"),
    ]
    payload = tmp_path / "inputs.json"
    payload.write_text(json.dumps({"datasets": inputs, "recipe": permissive()}))
    script = "import json,sys; from data_curator.pipeline import run_pipeline; p=json.load(open(sys.argv[1])); print(json.dumps(run_pipeline(p['datasets'],p['recipe']),sort_keys=True))"
    outputs = [
        subprocess.check_output(
            [sys.executable, "-c", script, str(payload)],
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": "src"},
        )
        for seed in ("1", "98765")
    ]
    assert outputs[0] == outputs[1]


def test_empty_and_all_rejected_runs_have_explicit_empty_artifacts():
    for source in (dataset("empty", []), dataset("bad", ["", "\x00", "tiny"])):
        result = run_pipeline([source], default_recipe())
        assert result["records"] == []
        assert result["summary"]["output_records"] == 0
        assert result["summary"]["shortage_records"] == 1000
        assert result["manifest"]["output_sha256"] == hashlib.sha256(b"").hexdigest()
        assert len(result["summary"]["stages"]) == 4


def test_html_content_remains_literal_text_and_never_executes(tmp_path):
    text = '<img src=x onerror="alert(1)"><script>throw new Error("untrusted")</script>'
    result = run_pipeline([dataset("html", [text])], permissive())
    assert result["records"][0]["text"] == text
    assert json.loads(export_jsonl(result["records"]))["text"] == text


@pytest.mark.parametrize(
    "raw",
    [
        {"unexpected": 1},
        {"clean": {"mim_chars": 2}},
        {"clean": {"min_chars": -1}},
        {"clean": {"min_chars": 99, "max_chars": 2}},
        {"clean": {"dedup_threshold": 0}},
        {"clean": {"holdout_overlap": float("nan")}},
        {"calibrate": {"quality_min": True}},
        {"compose": {"weights": {"a": 0}}},
        {"compose": {"weights": {"a": -1}}},
        {"compose": {"weights": {"a": float("inf")}}},
        {"compose": {"max_records": 0}},
        {"compose": {"max_records": 1.2}},
        {"seed": False},
        {"seed": -2},
        {"create": {"enabled": True}},
        {"create": {"retries": 3}},
        {"create": {"max_output_tokens": 8193}},
        {"create": {"enabled": "yes"}},
    ],
)
def test_invalid_configuration_rejected(raw):
    with pytest.raises(ValueError):
        validate_recipe(raw)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://169.254.169.254/v1",
        "http://localhost.evil/v1",
        "file:///etc/passwd",
        "http://u:p@127.0.0.1/v1",
        "https://127.0.0.1/v1",
    ],
)
def test_unsafe_enabled_model_endpoint_rejected_before_run(endpoint):
    with pytest.raises(ValueError):
        validate_recipe({"create": {"enabled": True, "model": "test", "endpoint": endpoint}})


def test_unknown_source_weight_and_invalid_dataset_roles_rejected():
    with pytest.raises(ValueError, match="source dataset IDs"):
        run_pipeline([dataset("source", ["text"])], permissive(compose={"weights": {"target": 1}}))
    with pytest.raises(ValueError, match="at least one source"):
        run_pipeline([dataset("target", ["text"], "target")], permissive())
    with pytest.raises(ValueError, match="more than once"):
        run_pipeline([dataset("source", []), dataset("source", [])], permissive())


def test_oversized_and_malformed_records_rejected_before_work():
    with pytest.raises(ValueError, match="200000"):
        run_pipeline([dataset("source", ["x" * 200001])], permissive())
    broken = dataset("source", ["normal source example"])
    broken["records"][0]["metadata"] = []
    with pytest.raises(ValueError, match="metadata"):
        run_pipeline([broken], permissive())
    with pytest.raises(ValueError, match="limit"):
        run_pipeline([dataset("source", ["x"] * 50001)], permissive())


def test_cancellation_before_and_during_pipeline_stops_without_results():
    source = dataset(
        "source",
        [f"Distinct source passage number {i}, describing experimental measurement." for i in range(80)],
    )
    with pytest.raises(CancelledError):
        run_pipeline([source], permissive(), cancel=lambda: True)
    events = []
    cancelled = False

    def progress(event):
        nonlocal cancelled
        events.append(event)
        if event["stage"] == "clean" and event["completed"] > 0:
            cancelled = True

    with pytest.raises(CancelledError):
        run_pipeline([source], permissive(), cancel=lambda: cancelled, progress=progress)
    assert events[-1]["stage"] == "clean"
    assert all(event["stage"] != "completed" for event in events)


def test_progress_reports_actual_stages_and_final_counts():
    events = []
    result = run_pipeline(
        [dataset("source", ["A stable reference text with no matching evaluation examples."])],
        permissive(),
        progress=events.append,
    )
    assert {event["stage"] for event in events} == {"clean", "calibrate", "create", "compose", "completed"}
    assert events[-1]["completed"] == len(result["records"])
    assert "estimate" in result["summary"]["token_count_method"]
