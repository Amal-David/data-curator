"""Focused core regressions from the independent VM review."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from data_curator import generation
from data_curator.ingest import ValidationError, parse_upload
from data_curator.pipeline import _apportion, _prepare, _tokens, default_recipe, export_jsonl, run_pipeline


def dataset(identifier, text, *, record_id="row", role="source", kind="text"):
    return {
        "id": identifier,
        "role": role,
        "records": [{"id": record_id, "text": text, "kind": kind, "metadata": {}}],
    }


def recipe(*, create=False):
    result = default_recipe()
    result["clean"].update(min_chars=1, dedup_threshold=1)
    result["calibrate"].update(quality_min=0)
    result["create"].update(enabled=create, model="fixture-model" if create else "")
    return result


def model_response(text="Freshly generated text about the structure of distant galaxies.", **message):
    return {"choices": [{"message": {"content": text, **message}, "finish_reason": "stop"}]}


def test_derived_ids_cannot_collide_when_components_contain_delimiters():
    sources = [
        dataset("a:b", "first = 1", record_id="c", kind="code"),
        dataset("a", "second = 2", record_id="b:c", kind="code"),
        dataset("a%3Ab", "third = 3", record_id="c", kind="code"),
    ]
    result = run_pipeline(sources, recipe())
    ids = [record["id"] for record in result["records"]]
    assert len(ids) == len(set(ids)) == 3
    assert {record["source_record_id"] for record in result["records"]} == {"c", "b:c"}
    assert len({event["record_id"] for event in result["decisions"] if event["stage"] == "compose"}) == 3


def test_supplementary_cjk_is_tokenized_per_character_and_excluded_before_generation(monkeypatch):
    reserved = "𠀀𠀁𠀂𠀃𠀄𠀅𠀆𠀇𠀈𠀉"
    assert _tokens(reserved) == list(reserved)
    received = []

    def fake(records, config, *, cancel=None):
        received.extend(records)
        return [], []

    monkeypatch.setattr(generation, "generate_records", fake)
    result = run_pipeline(
        [
            dataset("source", "𠀊" + reserved + "𠀋"),
            dataset("safe", "A source about boats and navigation."),
            dataset("heldout", reserved, role="holdout"),
            dataset("target", "Private calibration material.", role="target"),
        ],
        recipe(create=True),
    )
    assert [record["dataset_id"] for record in received] == ["safe"]
    assert result["summary"]["by_reason"] == {"holdout_overlap": 1}


def test_supplementary_cjk_generated_contamination_is_rejected(monkeypatch):
    reserved = "𠀀𠀁𠀂𠀃𠀄𠀅𠀆𠀇𠀈𠀉"
    monkeypatch.setattr(generation, "_request", lambda *args: model_response("𠀊" + reserved + "𠀋"))
    result = run_pipeline(
        [
            dataset("source", "A source about boats and navigation."),
            dataset("heldout", reserved, role="holdout"),
        ],
        recipe(create=True),
    )
    assert result["summary"]["synthetic_candidates"] == 1
    assert result["summary"]["synthetic_output_records"] == 0
    assert result["summary"]["by_reason"] == {"holdout_overlap": 1}
    assert reserved not in json.dumps(result["decisions"], ensure_ascii=False)
    candidate = result["generated_records"][0]
    assert "quality_score" not in candidate
    assert "relevance_score" not in candidate
    stages = {stage["stage"]: stage for stage in result["manifest"]["stage_hashes"]}
    assert stages["create"]["output_sha256"] == hashlib.sha256(b"").hexdigest()
    assert (
        result["manifest"]["generated_candidates_sha256"]
        == hashlib.sha256(export_jsonl(result["generated_records"])).hexdigest()
    )
    assert stages["create"]["output_sha256"] != result["manifest"]["generated_candidates_sha256"]


@pytest.mark.parametrize(
    "changes",
    [
        {"dataset_id": "other"},
        {"source_record_id": "invented"},
        {"kind": "code"},
        {"id": ""},
        {"lineage": {"origin": "original"}},
        {"lineage": {"parent_ids": ["target:row"]}},
        {"lineage": {"parent_ids": [["source:row"]]}},
        {"lineage": {"parent_ids": {"source:row": True}}},
        {"lineage": {"source_dataset_id": "other"}},
        {"lineage": {"source_record_id": "invented"}},
    ],
)
def test_generated_records_cannot_change_parent_identity_or_kind(monkeypatch, changes):
    def fake(records, config, *, cancel=None):
        parent = next(record for record in records if record["dataset_id"] == "source")
        generated = {
            **parent,
            "id": "synthetic-fixture",
            "text": "Fresh output about orbital mechanics and planetary trajectories.",
            "lineage": {"origin": "synthetic", "parent_ids": [parent["id"]]},
        }
        for key, value in changes.items():
            if key == "lineage":
                generated[key].update(value)
            else:
                generated[key] = value
        return [generated], []

    monkeypatch.setattr(generation, "generate_records", fake)
    with pytest.raises(ValueError, match="generated|Generated|source|parent|provenance"):
        run_pipeline(
            [
                dataset("source", "A source about boats and navigation."),
                dataset("other", "Another source about cooking and baking."),
                dataset("target", "Private calibration example.", role="target"),
            ],
            recipe(create=True),
        )


@pytest.mark.parametrize("field", ["temperature", "timeout_seconds"])
def test_generation_huge_numbers_fail_validation_without_overflow(field):
    with pytest.raises(generation.GenerationError, match=field):
        generation.generate_records([], {"enabled": True, "model": "test", field: 10**400})


@pytest.mark.parametrize("finish_reason", [None, "", "unexpected", "length", "tool_calls"])
def test_incomplete_or_unknown_generation_finish_reason_is_rejected(monkeypatch, finish_reason):
    response = model_response()
    response["choices"][0]["finish_reason"] = finish_reason
    monkeypatch.setattr(generation, "_request", lambda *args: response)
    with pytest.raises(generation.GenerationError, match="complete"):
        run_pipeline([dataset("source", "A source about boats and navigation.")], recipe(create=True))


def test_legacy_function_call_with_content_is_rejected(monkeypatch):
    monkeypatch.setattr(
        generation,
        "_request",
        lambda *args: model_response(function_call={"name": "untrusted", "arguments": "{}"}),
    )
    with pytest.raises(generation.GenerationError, match="tool call"):
        run_pipeline([dataset("source", "A source about boats and navigation.")], recipe(create=True))


def test_model_response_invalid_unicode_is_explicit(monkeypatch):
    monkeypatch.setattr(generation, "_request", lambda *args: model_response("invalid \ud800 text"))
    with pytest.raises(generation.GenerationError, match="Unicode"):
        run_pipeline([dataset("source", "A source about boats and navigation.")], recipe(create=True))


def test_upload_integer_decoder_limit_is_a_validation_error():
    content = b'{"text":"sample","metadata":{"n":' + b"1" * 5000 + b"}}"
    with pytest.raises(ValidationError, match="invalid JSON"):
        parse_upload(content, "large-number.jsonl")


def test_deeply_nested_model_json_is_an_explicit_generation_error(monkeypatch):
    body = b'{"nested":' + b"[" * 10_000 + b"0" + b"]" * 10_000 + b"}"

    class Response:
        status = 200

        def getheader(self, name):
            return str(len(body))

        def read(self, maximum):
            return body[:maximum]

    class Connection:
        sock = None

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(generation.http.client, "HTTPConnection", lambda *args, **kwargs: Connection())
    with pytest.raises(generation.GenerationError, match="invalid UTF-8 JSON"):
        generation._request("127.0.0.1", 11434, "/v1/chat/completions", b"{}", 1, None)


def test_stage_snapshots_capture_before_later_mutation_and_are_reproducible(monkeypatch):
    monkeypatch.setattr(generation, "_request", lambda *args: model_response())
    inputs = [
        dataset("source", "A source about boats and navigation."),
        dataset("target", "boats navigation", role="target"),
        dataset("heldout", "Reserved evaluation phrase withheld from training.", role="holdout"),
    ]
    first = run_pipeline(inputs, recipe(create=True))
    repeated = run_pipeline(list(reversed(inputs)), recipe(create=True))
    assert first == repeated
    stages = {stage["stage"]: stage for stage in first["manifest"]["stage_hashes"]}
    assert list(stages) == ["clean", "calibrate", "create", "compose"]
    original = next(record for record in first["records"] if record["lineage"]["origin"] == "original")
    unscored = copy.deepcopy(original)
    unscored.pop("quality_score")
    unscored.pop("relevance_score")

    def sha(records):
        return hashlib.sha256(export_jsonl(records)).hexdigest()

    def object_sha(value):
        content = json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    assert stages["clean"]["output_sha256"] == sha([unscored])
    assert stages["calibrate"]["output_sha256"] == sha([original])
    assert stages["create"]["output_sha256"] == sha(first["generated_records"])
    assert stages["compose"]["output_sha256"] == first["manifest"]["output_sha256"]
    assert stages["clean"]["output_sha256"] != stages["calibrate"]["output_sha256"]
    groups, _ = _prepare(inputs, None)
    assert stages["clean"]["input_sha256"] == object_sha(
        {"sources": groups["source"], "holdouts": groups["holdout"]}
    )
    assert stages["clean"]["config_sha256"] == object_sha(recipe(create=True)["clean"])
    assert stages["calibrate"]["input_sha256"] == object_sha(
        {"sources": [unscored], "targets": groups["target"]}
    )
    assert stages["calibrate"]["config_sha256"] == object_sha(recipe(create=True)["calibrate"])
    assert stages["create"]["input_sha256"] == object_sha(
        {
            "sources": [original],
            "cleaned_sources": [original],
            "targets": groups["target"],
            "holdouts": groups["holdout"],
        }
    )
    assert stages["compose"]["input_sha256"] == object_sha(
        {"records": [original] + first["generated_records"]}
    )
    for stage in stages.values():
        for key in ("input_sha256", "config_sha256", "output_sha256"):
            assert len(stage[key]) == 64


def test_stage_hashes_record_skipped_generation_and_seed_configuration():
    inputs = [dataset("source", "A source about boats and navigation.")]
    config = recipe()
    first = run_pipeline(inputs, config)
    config["seed"] += 1
    second = run_pipeline(inputs, config)
    stages_a = {stage["stage"]: stage for stage in first["manifest"]["stage_hashes"]}
    stages_b = {stage["stage"]: stage for stage in second["manifest"]["stage_hashes"]}
    assert stages_a["create"]["output_sha256"] == hashlib.sha256(b"").hexdigest()
    assert stages_a["compose"]["config_sha256"] != stages_b["compose"]["config_sha256"]
    assert stages_a["clean"] == stages_b["clean"]
    assert stages_a["calibrate"] == stages_b["calibrate"]


def test_extreme_valid_weights_redistribute_without_overflow_or_replacement():
    weights = {"zero": 0.0, "tiny": 5e-324, "large": 1e12}
    capacity = {"zero": 10, "tiny": 8, "large": 2}
    assert _apportion(7, weights, capacity) == {"zero": 0, "tiny": 5, "large": 2}
    assert _apportion(20, weights, capacity) == {"zero": 0, "tiny": 8, "large": 2}
    assert _apportion(7, dict(reversed(list(weights.items()))), capacity) == _apportion(7, weights, capacity)
