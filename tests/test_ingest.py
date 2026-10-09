"""Untrusted upload validation and preservation tests."""

import csv
import io
import json

import pytest

from data_curator import ingest
from data_curator.ingest import ValidationError, parse_upload


def test_jsonl_preserves_input_id_metadata_and_unicode():
    data = {
        "id": "../../untrusted",
        "text": "数据库事务。∑ x² ≥ 0",
        "metadata": {"license": "CC0-1.0"},
        "topic": "数学",
    }
    records = parse_upload((json.dumps(data, ensure_ascii=False) + "\n\n").encode(), "data.jsonl")
    assert records[0]["text"] == data["text"]
    assert records[0]["metadata"] == {"license": "CC0-1.0", "input_id": "../../untrusted", "topic": "数学"}
    assert "/" not in records[0]["id"]


def test_code_preserves_case_indentation_and_line_endings():
    content = b"def f(User):\r\n\tif User:\r\n\t\treturn User\r\n"
    record = parse_upload(content, "../../sample.py")[0]
    assert record["text"].encode() == content
    assert record["kind"] == "code"
    assert parse_upload(content.lower(), "sample.py")[0]["id"] != record["id"]


def test_json_array_and_custom_text_field():
    records = parse_upload(b'[{"body":"A safe sample", "kind":"code"}]', "data.json", text_field="body")
    assert records[0]["text"] == "A safe sample"
    assert records[0]["kind"] == "code"


def test_csv_quotes_multiline_and_metadata():
    content = 'body,kind,metadata,license\n"line one\nline two",code,"{""source"":""local""}",MIT\n'
    record = parse_upload(content.encode(), "code.csv", text_field="body")[0]
    assert record["text"] == "line one\nline two"
    assert record["metadata"] == {"source": "local", "license": "MIT"}


def test_csv_supports_valid_fields_above_default_python_csv_limit():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["text"])
    writer.writerow(["a" * 150_000])
    assert len(parse_upload(output.getvalue().encode(), "large.csv")[0]["text"]) == 150_000


@pytest.mark.parametrize(
    ("content", "filename", "message"),
    [
        (b"", "empty.txt", "empty"),
        (b" \n\t", "empty.jsonl", "empty"),
        (b"\xff", "bad.txt", "UTF-8"),
        (b'{"text":', "bad.jsonl", "Line 1"),
        (b'{"text":"ok"}\n{"text":4}', "bad.jsonl", "Record 2"),
        (b'{"text":"ok"}', "bad.json", "array"),
        (b"[]", "bad.json", "at least one"),
        (b'["text"]', "bad.json", "object"),
        (b'{"text":null}', "bad.jsonl", "string"),
        (b'{"text":"ok","metadata":[]}', "bad.jsonl", "metadata must be an object"),
        (b'{"text":"ok","kind":"binary"}', "bad.jsonl", "kind"),
        (b'{"text":"ok","score":NaN}', "bad.jsonl", "Non-finite"),
        (b'{"text":"ok","score":Infinity}', "bad.jsonl", "Non-finite"),
        (b'{"text":"\\ud800"}', "bad.jsonl", "Unicode"),
        (b"text,metadata\nok,[]\n", "bad.csv", "metadata"),
        (b"body\nok\n", "bad.csv", "column"),
        (b"text,text\na,b\n", "bad.csv", "unique"),
        (b"text\na,b\n", "bad.csv", "column count"),
        (b'print("hello")', "script.exe", "Unsupported"),
    ],
)
def test_invalid_inputs_are_explicit(content, filename, message):
    with pytest.raises(ValidationError, match=message):
        parse_upload(content, filename)


def test_record_limit_is_enforced_before_return(monkeypatch):
    monkeypatch.setattr(ingest, "MAX_RECORDS", 2)
    with pytest.raises(ValidationError, match="exceeds 2"):
        parse_upload(b'{"text":"one"}\n{"text":"two"}\n{"text":"three"}', "many.jsonl")


def test_upload_limit_is_enforced_before_decoding(monkeypatch):
    monkeypatch.setattr(ingest, "MAX_UPLOAD_BYTES", 4)
    with pytest.raises(ValidationError, match="limit"):
        parse_upload(b"12345", "large.txt")


def test_character_and_metadata_limits(monkeypatch):
    monkeypatch.setattr(ingest, "MAX_RECORD_CHARS", 3)
    with pytest.raises(ValidationError, match="characters"):
        parse_upload(b"four", "large.txt")
    monkeypatch.setattr(ingest, "MAX_METADATA_BYTES", 8)
    with pytest.raises(ValidationError, match="metadata exceeds"):
        parse_upload(b'{"text":"ok","metadata":{"long":"value"}}', "large.jsonl")


def test_metadata_nesting_is_bounded():
    nested = {}
    current = nested
    for _ in range(12):
        current["child"] = {}
        current = current["child"]
    with pytest.raises(ValidationError, match="nesting"):
        parse_upload(json.dumps({"text": "ok", "metadata": nested}).encode(), "deep.jsonl")


def test_markup_is_preserved_as_text_not_evaluated():
    text = "<script>globalThis.compromised=true</script>"
    assert parse_upload(text.encode(), "sample.txt")[0]["text"] == text


def test_bom_is_supported_without_changing_body():
    assert parse_upload(b'\xef\xbb\xbf{"text":"safe"}', "bom.jsonl")[0]["text"] == "safe"
