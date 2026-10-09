"""Small original fixtures with deliberately planted curation decisions."""

from __future__ import annotations

import json
from typing import Any


def _jsonl(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)


def demo_datasets() -> list[dict[str, Any]]:
    """Return fresh fixtures each time; no network or copyrighted source corpus."""
    shared = "Rain gardens capture runoff from roofs and paths. Their soil and deep-rooted plants slow the water, helping it soak into the ground."
    heldout = "The copper fox counted seven blue lanterns beside the silent observatory."
    sources = [
        {"id": "garden-1", "text": shared, "metadata": {"topic": "water", "license": "MIT"}},
        {
            "id": "garden-2",
            "text": "A soil moisture sensor measures water near plant roots. Compare readings over several days before changing an irrigation schedule.",
            "metadata": {"topic": "sensors", "license": "MIT"},
        },
        {
            "id": "garden-3",
            "text": "Compost adds organic matter that helps sandy soil retain moisture. Mix mature compost into the top layer without covering the stems of young plants.",
            "metadata": {"topic": "soil", "license": "MIT"},
        },
        {
            "id": "garden-4",
            "text": "雨水花园可以收集屋顶和小路的雨水。植物的根系让水慢慢渗入土壤，减少地面积水，并为昆虫提供栖息地。",
            "metadata": {"topic": "water", "language": "zh", "license": "MIT"},
        },
        {
            "id": "garden-math",
            "text": "A simple reservoir balance is ΔS = P − E − Q: storage changes with precipitation, evaporation, and drainage. All terms must use consistent units.",
            "metadata": {"topic": "water", "license": "MIT"},
        },
        {
            "id": "garden-junk",
            "text": "buy buy buy buy buy buy buy buy buy buy buy buy buy buy buy buy buy buy buy buy",
            "metadata": {"fixture": "repetition", "license": "MIT"},
        },
        {"id": "garden-short", "text": "??", "metadata": {"fixture": "short", "license": "MIT"}},
        {
            "id": "garden-contaminated",
            "text": "A field notebook included an unrelated evaluation sentence: "
            + heldout
            + " The remainder described a watering experiment.",
            "metadata": {"fixture": "holdout_overlap", "license": "MIT"},
        },
    ]
    field_notes = [
        {
            "id": "notes-duplicate",
            "text": shared,
            "metadata": {"fixture": "cross_source_duplicate", "license": "MIT"},
        },
        {
            "id": "notes-1",
            "text": "Mulch shades the soil and reduces evaporation. Leave a small gap around the plant stem so moisture does not remain against the bark.",
            "metadata": {"topic": "water", "license": "MIT"},
        },
        {
            "id": "notes-2",
            "text": "Measure rainfall with a straight-sided container placed away from trees. Record the depth at the same time each day and empty the container afterward.",
            "metadata": {"topic": "measurement", "license": "MIT"},
        },
        {
            "id": "notes-html",
            "text": "<script>alert('This is dataset text, never executable UI')</script> A garden log should record measurements and observations as plain text.",
            "metadata": {"fixture": "html_text", "license": "MIT"},
        },
        {
            "id": "notes-unrelated",
            "text": "A paper glider turns when one wing generates more lift than the other. Small folds in the trailing edge can change its flight path.",
            "metadata": {"topic": "aviation", "license": "MIT"},
        },
    ]
    code = [
        {
            "id": "code-water",
            "kind": "code",
            "text": "def water_needed(moisture, threshold=0.3):\n    if moisture < threshold:\n        return True\n    return False\n",
            "metadata": {"language": "python", "license": "MIT"},
        },
        {
            "id": "code-volume",
            "kind": "code",
            "text": "def collected_litres(area_m2, rain_mm):\n    # One millimetre over one square metre equals one litre.\n    return area_m2 * rain_mm\n",
            "metadata": {"language": "python", "license": "MIT"},
        },
        {
            "id": "code-case-upper",
            "kind": "code",
            "text": "def sensor_label():\n    label = 'WET'\n    return label\n",
            "metadata": {"fixture": "case_sensitive", "license": "MIT"},
        },
        {
            "id": "code-case-lower",
            "kind": "code",
            "text": "def sensor_label():\n    label = 'wet'\n    return label\n",
            "metadata": {"fixture": "case_sensitive", "license": "MIT"},
        },
    ]
    return [
        {
            "name": "Garden knowledge",
            "role": "source",
            "filename": "garden.jsonl",
            "content": _jsonl(sources),
            "license": "MIT",
        },
        {
            "name": "Field notes",
            "role": "source",
            "filename": "field-notes.jsonl",
            "content": _jsonl(field_notes),
            "license": "MIT",
        },
        {
            "name": "Sensor code",
            "role": "source",
            "filename": "sensor-code.jsonl",
            "kind": "code",
            "content": _jsonl(code),
            "license": "MIT",
        },
        {
            "name": "Garden task examples",
            "role": "target",
            "filename": "garden-target.jsonl",
            "content": _jsonl(
                [
                    {
                        "id": "target-1",
                        "text": "Explain practical garden water conservation, soil moisture measurement, rainfall collection, and irrigation.",
                    },
                    {
                        "id": "target-2",
                        "text": "Write simple sensor code for a garden that waters plants only when soil moisture is low.",
                    },
                ]
            ),
            "license": "MIT",
        },
        {
            "name": "Held-out evaluation",
            "role": "holdout",
            "filename": "garden-holdout.jsonl",
            "content": _jsonl([{"id": "holdout-1", "text": heldout}]),
            "license": "MIT",
        },
    ]
