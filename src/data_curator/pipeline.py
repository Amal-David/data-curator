"""Deterministic, inspectable local curation. No networking or code execution here.

The near-duplicate index is an original small-data implementation, not a model.
Text near-duplicates use token-shingle Jaccard; code uses exact, case- and
indentation-sensitive hashes only. All token counts are explicitly estimates.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

ENGINE_VERSION = "1.0.1"
MAX_RECORDS = 50_000
MAX_CHARACTERS = 20_000_000
_MAX_CANDIDATES = 256
_MINHASH_PRIME = (1 << 61) - 1
_MINHASH_COEFFICIENTS = tuple(
    (
        int.from_bytes(hashlib.sha256(f"curator-minhash-a-{i}".encode()).digest()[:8], "big")
        % (_MINHASH_PRIME - 1)
        + 1,
        int.from_bytes(hashlib.sha256(f"curator-minhash-b-{i}".encode()).digest()[:8], "big")
        % _MINHASH_PRIME,
    )
    for i in range(32)
)
# Include supplementary Han/Kana and decomposed/compatibility Hangul, rather
# than treating non-BMP ideographs as a single word in held-out screening.
_EAST_ASIAN_UNITS = (
    r"\u1100-\u11ff\u3007\u3040-\u30ff\u3130-\u318f\u31f0-\u31ff"
    r"\u3400-\u4dbf\u4e00-\u9fff\ua960-\ua97f\uac00-\ud7ff\uf900-\ufaff"
    r"\uff66-\uff9f\uffa0-\uffdc\U0001aff0-\U0001afff\U0001b000-\U0001b16f"
    r"\U00020000-\U0002ffff\U00030000-\U000323af"
)
_TOKEN_PATTERN = re.compile(rf"[{_EAST_ASIAN_UNITS}]|[^\W_{_EAST_ASIAN_UNITS}]+|[^\w\s]|_")


class CancelledError(Exception):
    """The caller cancelled the run at a safe checkpoint."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def export_jsonl(records: list[dict[str, Any]]) -> bytes:
    """Canonical UTF-8 JSONL, including a final newline for each record."""
    return b"".join(_canonical(record) + b"\n" for record in records)


def default_recipe() -> dict[str, Any]:
    return {
        "name": "Local curation recipe",
        "seed": 42,
        "clean": {
            "min_chars": 24,
            "max_chars": 200_000,
            "dedup_threshold": 0.9,
            "holdout_ngram": 8,
            "holdout_overlap": 0.2,
        },
        "calibrate": {"task": "", "quality_min": 0.35, "relevance_min": 0.0},
        "create": {
            "enabled": False,
            "endpoint": "http://127.0.0.1:11434/v1",
            "model": "",
            "max_records": 10,
            "temperature": 0.0,
            "max_output_tokens": 1024,
            "timeout_seconds": 30,
            "retries": 1,
        },
        "compose": {"max_records": 1000, "weights": {}},
    }


def _number(value: Any, name: str, low: float, high: float, *, integer: bool = False) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        raise ValueError(f"{name} must be a finite {'integer' if integer else 'number'}")
    if integer and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < low or value > high:
        raise ValueError(f"{name} must be between {low:g} and {high:g}")


def validate_recipe(raw: dict[str, Any]) -> dict[str, Any]:
    """Merge documented defaults and reject typos, unsafe shapes and bad numbers."""
    if not isinstance(raw, dict):
        raise ValueError("Recipe must be an object")
    result = default_recipe()
    unknown = set(raw) - set(result)
    if unknown:
        raise ValueError(f"Unknown recipe keys: {', '.join(sorted(unknown))}")
    for key, value in raw.items():
        if isinstance(result[key], dict):
            if not isinstance(value, dict):
                raise ValueError(f"{key} must be an object")
            extra = set(value) - set(result[key])
            if extra:
                raise ValueError(f"Unknown {key} keys: {', '.join(sorted(extra))}")
            result[key].update(copy.deepcopy(value))
        else:
            result[key] = copy.deepcopy(value)
    if not isinstance(result["name"], str) or not 1 <= len(result["name"]) <= 200:
        raise ValueError("name must be a nonempty string of at most 200 characters")
    _number(result["seed"], "seed", 0, 2**63 - 1, integer=True)
    clean = result["clean"]
    _number(clean["min_chars"], "clean.min_chars", 0, 200_000, integer=True)
    _number(clean["max_chars"], "clean.max_chars", 1, 200_000, integer=True)
    if clean["min_chars"] > clean["max_chars"]:
        raise ValueError("clean.min_chars cannot exceed clean.max_chars")
    _number(clean["dedup_threshold"], "clean.dedup_threshold", 0.01, 1)
    _number(clean["holdout_ngram"], "clean.holdout_ngram", 1, 32, integer=True)
    _number(clean["holdout_overlap"], "clean.holdout_overlap", 0.000001, 1)
    calibrate = result["calibrate"]
    if not isinstance(calibrate["task"], str) or len(calibrate["task"]) > 10_000:
        raise ValueError("calibrate.task must be a string of at most 10000 characters")
    for key in ("quality_min", "relevance_min"):
        _number(calibrate[key], f"calibrate.{key}", 0, 1)
    create = result["create"]
    if not isinstance(create["enabled"], bool):
        raise ValueError("create.enabled must be a boolean")
    for key, maximum in (("endpoint", 2048), ("model", 200)):
        if not isinstance(create[key], str) or len(create[key]) > maximum:
            raise ValueError(f"create.{key} must be a string of at most {maximum} characters")
    if create["enabled"] and not create["model"].strip():
        raise ValueError("An explicit local model is required when generation is enabled")
    if create["enabled"]:
        from .generation import validate_endpoint

        validate_endpoint(create["endpoint"])
    _number(create["max_records"], "create.max_records", 1, 1000, integer=True)
    _number(create["temperature"], "create.temperature", 0, 2)
    _number(create["max_output_tokens"], "create.max_output_tokens", 1, 8192, integer=True)
    _number(create["timeout_seconds"], "create.timeout_seconds", 1, 120)
    _number(create["retries"], "create.retries", 0, 2, integer=True)
    compose = result["compose"]
    _number(compose["max_records"], "compose.max_records", 1, MAX_RECORDS, integer=True)
    weights = compose["weights"]
    if not isinstance(weights, dict):
        raise ValueError("compose.weights must be an object")
    for key, weight in weights.items():
        if not isinstance(key, str) or not key:
            raise ValueError("Weight keys must be source dataset IDs")
        _number(weight, f"compose.weights.{key}", 0, 1e12)
    if weights and not any(weight > 0 for weight in weights.values()):
        raise ValueError("compose.weights must include at least one positive weight")
    return result


def _check(cancel: Callable[[], bool] | None) -> None:
    if cancel is not None and cancel():
        raise CancelledError("Run cancelled")


def _tokens(text: str, *, folded: bool = False) -> list[str]:
    # CJK characters are individual lexical units; math and punctuation stay visible.
    text = unicodedata.normalize("NFC", text)
    if folded:
        text = text.casefold()
    return _TOKEN_PATTERN.findall(text)


def estimated_tokens(text: str) -> int:
    """UTF-8 byte/4 estimate, not the output of a model tokenizer."""
    return math.ceil(len(text.encode("utf-8")) / 4)


def _quality(text: str, kind: str) -> tuple[float, dict[str, float]]:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0, {
            "meaningful": 0.0,
            "printable": 0.0,
            "length": 0.0,
            "diversity": 0.0,
            "repetition_factor": 0.0,
        }
    meaningful = sum(unicodedata.category(c)[0] in "LNS" for c in chars) / len(chars)
    printable = sum(c.isprintable() or c in "\n\r\t" for c in text) / max(len(text), 1)
    tokens = _tokens(text)
    length = min(1.0, math.log2(1 + len(chars)) / 8)
    diversity = min(1.0, len(set(chars)) / min(24, len(chars)))
    repetition = 1.0
    if len(tokens) >= 8:
        largest = max(Counter(tokens).values()) / len(tokens)
        repetition = min(1.0, (1 - largest) / 0.6)
    if len(chars) >= 12 and len(set(chars)) <= 2:
        repetition = min(repetition, 0.15)
    # Code punctuation and math symbols are expected, not evidence of poor language.
    base = 0.35 * meaningful + 0.2 * printable + 0.25 * length + 0.2 * diversity
    if kind == "code":
        base = 0.2 * meaningful + 0.3 * printable + 0.25 * length + 0.25 * diversity
    score = max(0.0, min(1.0, base * (0.25 + 0.75 * repetition)))
    return round(score, 6), {
        key: round(value, 6)
        for key, value in {
            "meaningful": meaningful,
            "printable": printable,
            "length": length,
            "diversity": diversity,
            "repetition_factor": repetition,
        }.items()
    }


def _exact_key(record: dict[str, Any]) -> str:
    # Deliberately no whitespace collapsing, case folding, or Unicode normalization.
    return _sha(record["kind"].encode() + b"\0" + record["text"].encode("utf-8"))


def _shingles(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    size = min(n, len(tokens))
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)} if size else set()


class _NearIndex:
    """32 deterministic MinHash permutations, 8 x 4 LSH bands, verified Jaccard.

    The first 512 indexed documents are also exhaustively compared. Larger indexes
    cap candidate verification at 256. This is a bounded heuristic, not exhaustive
    near-duplicate detection; exact SHA-256 deduplication has no such cap.
    """

    def __init__(self, threshold: float):
        self.threshold = threshold
        self.entries: list[tuple[str, set[tuple[str, ...]]]] = []
        self.bands: dict[tuple[int, tuple[int, ...]], list[int]] = defaultdict(list)

    def _signature(
        self, shingles: set[tuple[str, ...]], cancel: Callable[[], bool] | None
    ) -> tuple[int, ...]:
        values = [
            int.from_bytes(hashlib.sha256(_canonical(list(s))).digest()[:8], "big") % _MINHASH_PRIME
            for s in shingles
        ]
        # A deterministic bottom-hash cap bounds signature work on very large docs.
        values = sorted(values)[:8192]
        result = []
        for a, b in _MINHASH_COEFFICIENTS:
            _check(cancel)
            result.append(min(((a * value + b) % _MINHASH_PRIME for value in values), default=0))
        return tuple(result)

    def check_and_add(
        self, record: dict[str, Any], cancel: Callable[[], bool] | None
    ) -> dict[str, Any] | None:
        if record["kind"] == "code":
            return None
        shingles = _shingles(_tokens(record["text"]), 3)
        if not shingles:
            return None
        signature = self._signature(shingles, cancel)
        candidates: set[int] = set()
        if len(self.entries) <= 512:
            candidates.update(range(len(self.entries)))
        else:
            for band in range(8):
                candidates.update(self.bands.get((band, signature[band * 4 : (band + 1) * 4]), []))
        cap = 513 if len(self.entries) <= 512 else _MAX_CANDIDATES
        for index in sorted(candidates)[:cap]:
            _check(cancel)
            other_id, other = self.entries[index]
            # A cheap upper bound skips impossible matches without approximation.
            if min(len(shingles), len(other)) / max(len(shingles), len(other)) < self.threshold:
                continue
            jaccard = len(shingles & other) / len(shingles | other)
            if jaccard >= self.threshold:
                return {"duplicate_of": other_id, "jaccard": round(jaccard, 6), "threshold": self.threshold}
        index = len(self.entries)
        self.entries.append((record["id"], shingles))
        for band in range(8):
            self.bands[(band, signature[band * 4 : (band + 1) * 4])].append(index)
        return None


class _HoldoutIndex:
    def __init__(
        self, records: list[dict[str, Any]], n: int, overlap: float, cancel: Callable[[], bool] | None
    ):
        self.overlap = overlap
        self.references: list[tuple[str, int, int]] = []
        self.postings: dict[int, dict[tuple[str, ...], list[int]]] = defaultdict(lambda: defaultdict(list))
        for record in records:
            _check(cancel)
            tokens = _tokens(record["text"], folded=True)
            grams = _shingles(tokens, n)
            if not grams:
                continue
            width = min(n, len(tokens))
            index = len(self.references)
            self.references.append((record["id"], len(grams), width))
            for gram in grams:
                self.postings[width][gram].append(index)

    def match(self, text: str, cancel: Callable[[], bool] | None) -> dict[str, Any] | None:
        tokens = _tokens(text, folded=True)
        matches: Counter[int] = Counter()
        sizes: dict[int, int] = {}
        for width, postings in self.postings.items():
            _check(cancel)
            if len(tokens) < width:
                continue
            grams = _shingles(tokens, width)
            sizes[width] = len(grams)
            for gram in grams:
                matches.update(postings.get(gram, []))
        best: tuple[float, int, int] | None = None
        for index, count in sorted(matches.items()):
            _, reference_size, width = self.references[index]
            denominator = min(reference_size, sizes[width])
            fraction = count / denominator
            if fraction >= self.overlap and (best is None or fraction > best[0]):
                best = (fraction, index, count)
        if best is None:
            return None
        fraction, index, count = best
        return {
            "holdout_record_id": self.references[index][0],
            "overlap_fraction": round(fraction, 6),
            "matched_ngrams": count,
            "ngram_size": self.references[index][2],
            "threshold": self.overlap,
        }


class _Relevance:
    def __init__(
        self,
        sources: list[dict[str, Any]],
        targets: list[dict[str, Any]],
        task: str,
        cancel: Callable[[], bool] | None,
    ):
        self.active = bool(task.strip() or targets)
        self.idf: dict[str, float] = {}
        self.reference: dict[str, float] = {}
        if not self.active:
            return
        documents = []
        for record in sources + targets:
            _check(cancel)
            documents.append(Counter(_tokens(record["text"], folded=True)))
        if task.strip():
            documents.append(Counter(_tokens(task, folded=True)))
        frequencies: Counter[str] = Counter()
        for document in documents:
            frequencies.update(document.keys())
        self.idf = {
            token: math.log((1 + len(documents)) / (1 + count)) + 1 for token, count in frequencies.items()
        }
        reference: Counter[str] = Counter(_tokens(task, folded=True))
        for record in targets:
            reference.update(_tokens(record["text"], folded=True))
        self.reference = self._vector(reference)

    def _vector(self, counts: Counter[str]) -> dict[str, float]:
        values = {
            token: (1 + math.log(count)) * self.idf.get(token, 1.0)
            for token, count in counts.items()
            if count
        }
        length = math.sqrt(sum(value * value for value in values.values()))
        return {token: value / length for token, value in values.items()} if length else {}

    def score(self, text: str) -> float:
        if not self.active:
            return 1.0
        vector = self._vector(Counter(_tokens(text, folded=True)))
        return round(
            max(
                0.0, min(1.0, sum(value * self.reference.get(token, 0.0) for token, value in vector.items()))
            ),
            6,
        )


def _apportion(
    total: int, weights: dict[str, float], capacities: dict[str, int] | None = None
) -> dict[str, int]:
    allocated = dict.fromkeys(weights, 0)
    remaining = total
    while remaining:
        eligible = {
            key: weight
            for key, weight in weights.items()
            if weight > 0 and (capacities is None or allocated[key] < capacities[key])
        }
        if not eligible:
            break
        denominator = math.fsum(eligible.values())
        exact = {key: remaining * (weight / denominator) for key, weight in eligible.items()}
        increments = {key: math.floor(value) for key, value in exact.items()}
        residue = remaining - sum(increments.values())
        for key in sorted(eligible, key=lambda item: (-(exact[item] - increments[item]), item))[:residue]:
            increments[key] += 1
        consumed = 0
        for key, amount in increments.items():
            if capacities is not None:
                amount = min(amount, capacities[key] - allocated[key])
            allocated[key] += amount
            consumed += amount
        if not consumed:
            break
        remaining -= consumed
    return allocated


def _prepare(
    datasets: list[dict[str, Any]], cancel: Callable[[], bool] | None
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("Select at least one source dataset")
    groups: dict[str, list[dict[str, Any]]] = {"source": [], "target": [], "holdout": []}
    inputs = []
    seen_datasets: set[str] = set()
    total_chars = total_records = 0
    for dataset in datasets:
        _check(cancel)
        if not isinstance(dataset, dict) or not isinstance(dataset.get("id"), str) or not dataset["id"]:
            raise ValueError("Each dataset needs a nonempty string ID")
        dataset_id = dataset["id"]
        if dataset_id in seen_datasets:
            raise ValueError("A dataset cannot be selected more than once")
        seen_datasets.add(dataset_id)
        role = dataset.get("role")
        if role not in groups:
            raise ValueError("Dataset role must be source, target, or holdout")
        records = dataset.get("records")
        if not isinstance(records, list):
            raise ValueError("Dataset records must be a list")
        total_records += len(records)
        if total_records > MAX_RECORDS:
            raise ValueError(f"Local engine limit is {MAX_RECORDS} total input records")
        ids: set[str] = set()
        for record in records:
            _check(cancel)
            if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
                raise ValueError("Each input record needs a nonempty string ID")
            if record["id"] in ids:
                raise ValueError("Record IDs must be unique within a dataset")
            ids.add(record["id"])
            if not isinstance(record.get("text"), str):
                raise ValueError("Record text must be a string")
            kind = record.get("kind", "text")
            if kind not in ("text", "code"):
                raise ValueError("Record kind must be text or code")
            metadata = record.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ValueError("Record metadata must be an object")
            if len(record["text"]) > 200_000:
                raise ValueError("Input records cannot exceed 200000 characters")
            total_chars += len(record["text"])
            if total_chars > MAX_CHARACTERS:
                raise ValueError(f"Local engine limit is {MAX_CHARACTERS} total input characters")
            derived = copy.deepcopy(record)
            derived.update(
                {
                    "id": ":".join(
                        value.replace("%", "%25").replace(":", "%3A") for value in (dataset_id, record["id"])
                    ),
                    "dataset_id": dataset_id,
                    "source_record_id": record["id"],
                    "kind": kind,
                    "metadata": copy.deepcopy(metadata),
                    "content_sha256": _sha(record["text"].encode("utf-8")),
                    "lineage": {
                        "origin": "original",
                        "source_dataset_id": dataset_id,
                        "source_record_id": record["id"],
                        "parent_ids": [],
                    },
                }
            )
            derived["metadata"].setdefault("license", dataset.get("license", "unknown"))
            groups[role].append(derived)
        inputs.append(
            {
                "dataset_id": dataset_id,
                "role": role,
                "records": len(records),
                "upload_sha256": dataset.get("sha256"),
                "records_sha256": _sha(export_jsonl(records)),
                "license": dataset.get("license", "unknown"),
            }
        )
    if not any(dataset["role"] == "source" for dataset in datasets):
        raise ValueError("Select at least one source dataset")
    for records in groups.values():
        records.sort(key=lambda record: (record["dataset_id"], record["source_record_id"]))
    return groups, sorted(inputs, key=lambda item: item["dataset_id"])


def run_pipeline(
    datasets: list[dict[str, Any]],
    recipe: dict[str, Any],
    *,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the same pure curation engine from CLI or API against immutable inputs."""
    config = validate_recipe(recipe)
    _check(cancel)
    groups, inputs = _prepare(datasets, cancel)
    sources, targets, holdouts = groups["source"], groups["target"], groups["holdout"]
    source_ids = sorted(dataset["id"] for dataset in datasets if dataset["role"] == "source")
    configured_weights = config["compose"]["weights"]
    unknown_sources = set(configured_weights) - set(source_ids)
    if unknown_sources:
        raise ValueError("Composition weights must refer only to selected source dataset IDs")
    weights = (
        {key: float(configured_weights.get(key, 0)) for key in source_ids}
        if configured_weights
        else dict.fromkeys(source_ids, 1.0)
    )
    decisions: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    stage_hashes: list[dict[str, str]] = []

    def begin_stage(stage: str, inputs: dict[str, Any], stage_config: dict[str, Any]) -> dict[str, str]:
        # Hash now: later calibration adds scores to the same derived records.
        snapshot = {
            "stage": stage,
            "input_sha256": _sha(_canonical(inputs)),
            "config_sha256": _sha(_canonical(stage_config)),
        }
        stage_hashes.append(snapshot)
        return snapshot

    def report(stage: str, completed: int, total: int, message: str) -> None:
        _check(cancel)
        if progress:
            progress({"stage": stage, "completed": completed, "total": total, "message": message})

    def decision(
        record: dict[str, Any], stage: str, action: str, reason: str, details: dict[str, Any] | None = None
    ) -> None:
        entry = {
            "record_id": record["id"],
            "dataset_id": record["dataset_id"],
            "stage": stage,
            "action": action,
            "reason": reason,
            "details": details or {},
        }
        decisions.append(entry)
        if action == "reject":
            rejected.append(
                {**record, "rejection": {"stage": stage, "reason": reason, "details": details or {}}}
            )

    clean_config = config["clean"]
    clean_hashes = begin_stage("clean", {"sources": sources, "holdouts": holdouts}, clean_config)
    report("clean", 0, len(sources), "Indexing held-out references and checking source records")
    holdout_index = _HoldoutIndex(
        holdouts, clean_config["holdout_ngram"], clean_config["holdout_overlap"], cancel
    )
    exact: dict[str, str] = {}
    near = _NearIndex(clean_config["dedup_threshold"])
    cleaned = []

    def screening(record: dict[str, Any], stage: str) -> bool:
        _check(cancel)
        text = record["text"]
        if not text.strip():
            decision(record, stage, "reject", "empty_text")
            return False
        if len(text) < clean_config["min_chars"] or len(text) > clean_config["max_chars"]:
            decision(
                record,
                stage,
                "reject",
                "character_length",
                {"characters": len(text), "min": clean_config["min_chars"], "max": clean_config["max_chars"]},
            )
            return False
        controls = sum(unicodedata.category(char) == "Cc" and char not in "\n\r\t" for char in text)
        if controls:
            decision(record, stage, "reject", "control_characters", {"count": controls})
            return False
        overlap = holdout_index.match(text, cancel)
        if overlap:
            decision(record, stage, "reject", "holdout_overlap", overlap)
            return False
        key = _exact_key(record)
        if key in exact:
            decision(
                record,
                stage,
                "reject",
                "exact_duplicate",
                {"duplicate_of": exact[key], "content_sha256": record["content_sha256"]},
            )
            return False
        duplicate = near.check_and_add(record, cancel)
        if duplicate:
            decision(record, stage, "reject", "near_duplicate", duplicate)
            return False
        exact[key] = record["id"]
        return True

    for index, record in enumerate(sources):
        if screening(record, "clean"):
            cleaned.append(record)
            decision(record, "clean", "keep", "passed_cleaning")
        if index % 32 == 0 or index + 1 == len(sources):
            report("clean", index + 1, len(sources), "Checking exact/near duplicates and held-out overlap")
    clean_hashes["output_sha256"] = _sha(export_jsonl(cleaned))
    stages.append(
        {
            "stage": "clean",
            "input": len(sources),
            "output": len(cleaned),
            "description": "Length/control checks, code-safe exact hashes, verified text Jaccard and held-out n-grams",
        }
    )
    calibration_hashes = begin_stage(
        "calibrate", {"sources": cleaned, "targets": targets}, config["calibrate"]
    )
    report("calibrate", 0, len(cleaned), "Fitting transparent TF-IDF relevance from sources and targets only")
    relevance = _Relevance(cleaned, targets, config["calibrate"]["task"], cancel)
    calibrated = []

    def calibrate(record: dict[str, Any], stage: str) -> bool:
        _check(cancel)
        quality, components = _quality(record["text"], record["kind"])
        record["quality_score"] = quality
        record["relevance_score"] = relevance.score(record["text"])
        details = {
            "quality_score": quality,
            "quality_components": components,
            "relevance_score": record["relevance_score"],
            "relevance_active": relevance.active,
        }
        if quality < config["calibrate"]["quality_min"]:
            decision(record, stage, "reject", "low_quality", details)
            return False
        if record["relevance_score"] < config["calibrate"]["relevance_min"]:
            decision(record, stage, "reject", "low_relevance", details)
            return False
        decision(record, stage, "keep", "passed_calibration", details)
        return True

    for index, record in enumerate(cleaned):
        if calibrate(record, "calibrate"):
            calibrated.append(record)
        if index % 32 == 0 or index + 1 == len(cleaned):
            report("calibrate", index + 1, len(cleaned), "Scoring Unicode-aware quality and target relevance")
    calibration_hashes["output_sha256"] = _sha(export_jsonl(calibrated))
    stages.append(
        {
            "stage": "calibrate",
            "input": len(cleaned),
            "output": len(calibrated),
            "description": "Inspectable heuristic quality and TF-IDF cosine relevance; held-out text excluded",
        }
    )
    generated: list[dict[str, Any]] = []
    generated_candidates: list[dict[str, Any]] = []
    creation_hashes = begin_stage(
        "create",
        {"sources": calibrated, "cleaned_sources": cleaned, "targets": targets, "holdouts": holdouts},
        {"create": config["create"], "clean": clean_config, "calibrate": config["calibrate"]},
    )
    skipped = []
    if not config["create"]["enabled"]:
        skipped.append({"stage": "create", "reason": "Generation disabled; no model was called"})
        report("create", 0, 0, "Skipped: generation is disabled")
    elif not calibrated:
        skipped.append({"stage": "create", "reason": "No eligible source records remain"})
        report("create", 0, 0, "Skipped: no eligible source records")
    else:
        from .generation import GenerationCancelled, generate_records

        report(
            "create",
            0,
            min(config["create"]["max_records"], len(calibrated)),
            "Calling explicitly configured local model",
        )
        try:
            generated_candidates, generation_decisions = generate_records(
                copy.deepcopy(calibrated), config["create"], cancel=cancel
            )
        except GenerationCancelled as error:
            raise CancelledError(str(error)) from error
        _check(cancel)
        decisions.extend(generation_decisions)
        seen_ids = {record["id"] for record in sources + targets + holdouts}
        parents = {record["id"]: record for record in calibrated}
        for index, record in enumerate(generated_candidates):
            _check(cancel)
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("text"), str)
                or not isinstance(record.get("id"), str)
                or not record["id"]
            ):
                raise ValueError("Local model adapter returned an invalid generated record")
            if record["id"] in seen_ids:
                raise ValueError("Local model adapter returned a duplicate generated record ID")
            seen_ids.add(record["id"])
            if record.get("dataset_id") not in source_ids or record.get("kind") not in ("text", "code"):
                raise ValueError("Local model adapter returned invalid source provenance")
            lineage = record.get("lineage", {})
            parent_ids = lineage.get("parent_ids", []) if isinstance(lineage, dict) else []
            # Generation adapters must preserve a source-grounded parent, not invent one.
            if (
                not isinstance(parent_ids, list)
                or len(parent_ids) != 1
                or not isinstance(parent_ids[0], str)
                or parent_ids[0] not in parents
            ):
                raise ValueError("Generated record must identify eligible source parent IDs")
            parent = parents[parent_ids[0]]
            if (
                lineage.get("origin") != "synthetic"
                or any(record.get(key) != parent[key] for key in ("dataset_id", "source_record_id", "kind"))
                or lineage.get("source_dataset_id", parent["dataset_id"]) != parent["dataset_id"]
                or lineage.get("source_record_id", parent["source_record_id"]) != parent["source_record_id"]
            ):
                raise ValueError("Generated record must preserve its source parent's identity and kind")
            record["content_sha256"] = _sha(record["text"].encode("utf-8"))
            # Adapter copies can carry parent scores. A candidate rejected by
            # cleaning has not been scored and must not inherit those values.
            record.pop("quality_score", None)
            record.pop("relevance_score", None)
            if screening(record, "create") and calibrate(record, "create"):
                generated.append(record)
            report(
                "create",
                index + 1,
                len(generated_candidates),
                "Re-screening generated text for contamination, duplicates and scores",
            )
    creation_hashes["output_sha256"] = _sha(export_jsonl(generated))
    stages.append(
        {
            "stage": "create",
            "input": len(calibrated),
            "output": len(generated),
            "description": "Optional local source-grounded generation, followed by the same safety and quality screens"
            if config["create"]["enabled"]
            else "Skipped: disabled by default",
        }
    )
    candidates = calibrated + generated
    composition_hashes = begin_stage(
        "compose",
        {"records": candidates},
        {"compose": config["compose"], "seed": config["seed"], "resolved_weights": weights},
    )
    report("compose", 0, len(candidates), "Allocating a deterministic record budget without replacement")
    buckets: dict[str, list[dict[str, Any]]] = {key: [] for key in source_ids}
    for record in candidates:
        buckets[record["dataset_id"]].append(record)
    capacity = {key: len(records) for key, records in buckets.items()}
    budget = config["compose"]["max_records"]
    nominal = _apportion(budget, weights)
    quotas = _apportion(budget, weights, capacity)
    selected = []

    def rank(record: dict[str, Any], scope: str) -> str:
        return _sha(_canonical([config["seed"], scope, record["id"]]))

    for key in source_ids:
        _check(cancel)
        ordered = sorted(buckets[key], key=lambda record: (rank(record, "within_source"), record["id"]))
        for index, record in enumerate(ordered):
            _check(cancel)
            if index < quotas[key]:
                selected.append(record)
                decision(record, "compose", "select", "selected_without_replacement")
            else:
                decision(
                    record,
                    "compose",
                    "reject",
                    "zero_source_weight" if not weights[key] else "outside_record_budget",
                )
    selected.sort(key=lambda record: (rank(record, "export_order"), record["id"]))
    composition_hashes["output_sha256"] = _sha(export_jsonl(selected))
    stages.append(
        {
            "stage": "compose",
            "input": len(candidates),
            "output": len(selected),
            "description": "Seeded SHA-256 ordering with weighted largest-remainder quotas; no replacement",
        }
    )
    by_reason = dict(sorted(Counter(record["rejection"]["reason"] for record in rejected).items()))
    weight_total = math.fsum(weights.values())
    mixture = [
        {
            "dataset_id": key,
            "requested_weight": weights[key],
            "requested_fraction": round(weights[key] / weight_total, 8),
            "requested_records": nominal[key],
            "available_records": capacity[key],
            "realized_records": quotas[key],
            "realized_fraction": round(quotas[key] / len(selected), 8) if selected else 0.0,
            "source_shortage": max(0, nominal[key] - capacity[key]),
        }
        for key in source_ids
    ]
    summary = {
        "input_records": len(sources),
        "clean_records": len(cleaned),
        "calibrated_records": len(calibrated),
        "output_records": len(selected),
        "synthetic_records": len(generated),
        "synthetic_candidates": len(generated_candidates),
        "synthetic_output_records": sum(
            record.get("lineage", {}).get("origin") == "synthetic" for record in selected
        ),
        "rejected_records": len(rejected),
        "estimated_tokens_before": sum(estimated_tokens(record["text"]) for record in sources),
        "estimated_tokens_after": sum(estimated_tokens(record["text"]) for record in selected),
        "by_reason": by_reason,
        "stages": stages,
        "mixture": mixture,
        "requested_records": budget,
        "shortage_records": max(0, budget - len(selected)),
        "token_count_method": "ceil(UTF-8 bytes / 4), estimate; not a model tokenizer",
        "skipped_stages": skipped,
    }
    manifest = {
        "schema_version": 1,
        "engine": {"name": "data-curator", "version": ENGINE_VERSION},
        "seed": config["seed"],
        "recipe_sha256": _sha(_canonical(config)),
        "inputs": inputs,
        "output_sha256": _sha(export_jsonl(selected)),
        "rejected_sha256": _sha(export_jsonl(rejected)),
        "decisions_sha256": _sha(export_jsonl(decisions)),
        "generated_candidates_sha256": _sha(export_jsonl(generated_candidates)),
        "output_records": len(selected),
        "stage_counts": stages,
        "stage_hashes": stage_hashes,
        "mixture": mixture,
        "shortage_records": summary["shortage_records"],
        "skipped_stages": skipped,
        "token_count_method": summary["token_count_method"],
        "algorithms": {
            "stage_hashes": "entry-time canonical JSON input/config objects; exit-time canonical JSONL kept records v1",
            "exact_dedup": "sha256(kind + NUL + exact UTF-8); case and indentation preserved",
            "near_dedup": "text-only token 3-shingle Jaccard; deterministic MinHash32/LSH8x4; verify every candidate",
            "minhash_scheme": "sha256 token tuples, prime 2^61-1, sha256-derived affine coefficients v1, bottom8192",
            "near_candidate_limit": _MAX_CANDIDATES,
            "exhaustive_near_limit": 512,
            "holdout": "Unicode casefolded token n-grams; overlap / min(document grams, reference grams)",
            "quality": "Unicode category, printable, length, diversity and repetition heuristics v1",
            "relevance": "log-TF smooth-IDF cosine to task+target centroid v1"
            if relevance.active
            else "unconfigured, score=1",
            "composition": "largest-remainder capacity redistribution + seeded SHA256 order v1",
            "replacement": False,
        },
        "limits": {
            "max_input_records": MAX_RECORDS,
            "max_input_characters": MAX_CHARACTERS,
            "execution": "in-memory, local small-data engine",
        },
        "reproducibility": "Deterministic stages are byte reproducible for identical IDs, records and recipe. Local model outputs can vary; preserve generated candidates as replay inputs.",
    }
    report("completed", len(selected), len(selected), "Curation complete")
    return {
        "records": selected,
        "rejected_records": rejected,
        "decisions": decisions,
        "summary": summary,
        "manifest": manifest,
        "generated_records": generated_candidates,
    }
