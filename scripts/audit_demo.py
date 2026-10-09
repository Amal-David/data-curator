"""Measure the original planted demo fixtures without a model or network access.

Run with: uv run python scripts/audit_demo.py --output docs/fixture-audit.json
Results validate fixture behavior only, never downstream training quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path

from data_curator.demo import demo_datasets
from data_curator.ingest import parse_upload
from data_curator.pipeline import default_recipe, export_jsonl, run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    datasets = []
    for index, fixture in enumerate(demo_datasets()):
        content = fixture["content"].encode("utf-8")
        datasets.append(
            {
                "id": f"fixture-{index:02d}",
                "name": fixture["name"],
                "role": fixture["role"],
                "license": fixture["license"],
                "sha256": hashlib.sha256(content).hexdigest(),
                "records": parse_upload(content, fixture["filename"], kind=fixture.get("kind", "text")),
            }
        )
    recipe = default_recipe()
    start = time.perf_counter()
    result = run_pipeline(datasets, recipe)
    elapsed = time.perf_counter() - start
    repeat = run_pipeline(datasets, recipe)
    output = export_jsonl(result["records"])
    assert output == export_jsonl(repeat["records"]), "Repeated output bytes differ"
    assert hashlib.sha256(output).hexdigest() == result["manifest"]["output_sha256"]
    repeat_artifacts = {}
    for key in ("records", "rejected_records", "decisions", "generated_records"):
        repeat_artifacts[key] = export_jsonl(result[key]) == export_jsonl(repeat[key])
    for key in ("summary", "manifest"):
        repeat_artifacts[key] = json.dumps(result[key], sort_keys=True) == json.dumps(repeat[key], sort_keys=True)
    assert all(repeat_artifacts.values()), "A repeated deterministic artifact differs"
    rejected = {
        record["metadata"].get("input_id"): record["rejection"]["reason"]
        for record in result["rejected_records"]
    }
    expected = {
        "garden-short": "character_length",
        "garden-contaminated": "holdout_overlap",
        "garden-junk": "low_quality",
        "notes-duplicate": "exact_duplicate",
    }
    assert rejected == expected, (rejected, expected)
    kept = {record["metadata"].get("input_id") for record in result["records"]}
    assert {"garden-4", "garden-math", "code-case-upper", "code-case-lower", "notes-html"} <= kept
    assert len({record["id"] for record in result["records"]}) == len(result["records"])
    assert not result["generated_records"]
    report = {
        "scope": "Original tiny planted fixtures; no model, network, training or scale claim",
        "recipe": recipe,
        "inputs": result["manifest"]["inputs"],
        "source_records": result["summary"]["input_records"],
        "curated_records": len(result["records"]),
        "expected_rejections": expected,
        "observed_rejections": rejected,
        "planted_rejection_precision": 1.0,
        "planted_rejection_recall": 1.0,
        "protected_examples_kept": ["CJK", "math", "code case and indentation", "inert HTML text"],
        "repeat_output_byte_identical": True,
        "repeat_artifacts_identical": repeat_artifacts,
        "engine": result["manifest"]["engine"],
        "stage_hashes": result["manifest"]["stage_hashes"],
        "output_sha256": result["manifest"]["output_sha256"],
        "token_count_method": result["manifest"]["token_count_method"],
        "single_run_seconds": round(elapsed, 6),
        "timing_environment": {
            "python": platform.python_version(),
            "os": platform.system(),
            "architecture": platform.machine(),
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
