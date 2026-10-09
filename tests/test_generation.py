from __future__ import annotations

import hashlib
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from data_curator.generation import GenerationCancelled, GenerationError, generate_records

SOURCE = {
    "id": "source-record-1",
    "dataset_id": "dataset-1",
    "source_record_id": "source-record-1",
    "kind": "text",
    "text": "Rain gardens collect water and help it soak into the soil.",
    "metadata": {"private_note": "never include this metadata in a prompt"},
    "lineage": {"origin": "original"},
}


def answer(text: str = "Rain gardens capture water so that it can seep into the soil.") -> bytes:
    return json.dumps(
        {
            "model": "fixture-model-v1",
            "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        }
    ).encode()


@contextmanager
def model_server(responses: list[tuple[int, bytes]], delay: float = 0):
    calls: list[dict[str, Any]] = []
    requested = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(
                {
                    "path": self.path,
                    "body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                    "headers": dict(self.headers),
                }
            )
            requested.set()
            if delay:
                time.sleep(delay)
            status, body = responses[min(len(calls) - 1, len(responses) - 1)]
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if status == 302:
                    self.send_header("Location", "http://example.com/private")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls, requested
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def config(endpoint: str, **changes: Any) -> dict[str, Any]:
    return {
        "enabled": True,
        "endpoint": endpoint,
        "model": "fixture-model",
        "max_records": 1,
        "retries": 0,
        "timeout_seconds": 2,
        **changes,
    }


def test_disabled_never_connects_or_fakes_output():
    assert generate_records([SOURCE], {"enabled": False, "endpoint": "https://untrusted.example"}) == ([], [])


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1/v1",
        "http://localhost:11434/v1",
        "http://example.com/v1",
        "http://169.254.169.254/v1",
        "http://0.0.0.0/v1",
        "http://user:password@127.0.0.1/v1",
        "http://127.0.0.1/v1?api_key=secret",
        "http://127.0.0.1/v1#fragment",
        "file:///etc/passwd",
        "http://127.0.0.1:0/v1",
        "http://127.0.0.1:65536/v1",
        "http://127.0.0.1/download",
        "http://[::ffff:192.168.1.1]/v1",
    ],
)
def test_rejects_unsafe_endpoints_without_request(endpoint):
    with pytest.raises(GenerationError, match="endpoint|loopback"):
        generate_records([SOURCE], config(endpoint))


def test_requires_explicit_installed_model():
    with pytest.raises(GenerationError, match="installed local model"):
        generate_records([SOURCE], config("http://127.0.0.1:1/v1", model=""))


def test_generates_source_only_with_lineage_and_ignores_environment_proxies(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.1:9999")
    monkeypatch.setenv("ALL_PROXY", "http://192.0.2.1:9999")
    with model_server([(200, answer())]) as (endpoint, calls, _):
        output, decisions = generate_records([SOURCE], config(endpoint))
    assert len(output) == 1
    assert output[0]["id"].startswith("syn_")
    assert output[0]["id"] != SOURCE["id"]
    assert output[0]["lineage"]["parent_ids"] == [SOURCE["id"]]
    assert output[0]["lineage"]["source_sha256"] == hashlib.sha256(SOURCE["text"].encode()).hexdigest()
    assert output[0]["lineage"]["output_sha256"] == hashlib.sha256(output[0]["text"].encode()).hexdigest()
    assert output[0]["lineage"]["response_model"] == "fixture-model-v1"
    assert decisions[0]["action"] == "generate"
    assert SOURCE["lineage"] == {"origin": "original"}
    assert calls[0]["path"] == "/v1/chat/completions"
    assert calls[0]["body"]["stream"] is False
    messages = calls[0]["body"]["messages"]
    assert json.loads(messages[1]["content"]) == {"kind": "text", "source": SOURCE["text"]}
    assert "never include this metadata" not in json.dumps(messages)
    assert "Authorization" not in calls[0]["headers"]


def test_identical_stored_response_produces_same_id_and_prompt_hash():
    with model_server([(200, answer())]) as (endpoint, _, _):
        first, _ = generate_records([SOURCE], config(endpoint))
        second, _ = generate_records([SOURCE], config(endpoint))
    assert first == second


def test_preserves_generated_code_indentation_and_case():
    text = "def Water():\n    return 'WET'\n"
    with model_server([(200, answer(text))]) as (endpoint, _, _):
        output, _ = generate_records([{**SOURCE, "kind": "code"}], config(endpoint))
    assert output[0]["text"] == text


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (302, b"{}", "redirects are never followed"),
        (401, b"{}", "HTTP 401"),
        (200, b"invalid", "invalid UTF-8 JSON"),
        (200, b"[]", "JSON object"),
        (200, b'{"choices": []}', "choices"),
        (200, answer("  "), "empty"),
        (
            200,
            b'{"choices":[{"message":{"content":"truncated"},"finish_reason":"length"}]}',
            "did not complete",
        ),
        (200, b"x" * 1_048_577, "1 MiB limit"),
    ],
    ids=[
        "redirect",
        "unauthorized",
        "malformed",
        "not-object",
        "no-choices",
        "empty",
        "truncated",
        "oversized",
    ],
)
def test_model_failure_is_explicit_and_never_fabricated(status, body, message):
    with model_server([(status, body)]) as (endpoint, calls, _):
        with pytest.raises(GenerationError, match=message):
            generate_records([SOURCE], config(endpoint, retries=2))
    assert len(calls) == 1


def test_bounded_retry_on_temporary_failure():
    with model_server([(503, b"unavailable"), (200, answer())]) as (endpoint, calls, _):
        output, _ = generate_records([SOURCE], config(endpoint, retries=1))
    assert len(calls) == 2
    assert output[0]["lineage"]["attempts"] == 2


def test_retry_exhaustion_is_failure():
    with model_server([(503, b"unavailable")]) as (endpoint, calls, _):
        with pytest.raises(GenerationError, match=r"after 2 attempt\(s\)"):
            generate_records([SOURCE], config(endpoint, retries=1))
    assert len(calls) == 2


def test_cancellation_stops_waiting_for_slow_model():
    with model_server([(200, answer())], delay=2) as (endpoint, calls, requested):
        started = time.monotonic()
        with pytest.raises(GenerationCancelled):
            generate_records([SOURCE], config(endpoint, timeout_seconds=10), cancel=requested.is_set)
        assert time.monotonic() - started < 1
    assert len(calls) == 1


def test_timeout_has_wall_clock_bound():
    with model_server([(200, answer())], delay=2) as (endpoint, _, _):
        started = time.monotonic()
        with pytest.raises(GenerationError, match="timed out|connection failed"):
            generate_records([SOURCE], config(endpoint, timeout_seconds=0.15))
        assert time.monotonic() - started < 1


def test_cancel_before_generation_sends_nothing():
    with model_server([(200, answer())]) as (endpoint, calls, _):
        with pytest.raises(GenerationCancelled):
            generate_records([SOURCE], config(endpoint), cancel=lambda: True)
    assert calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", float("nan")),
        ("temperature", float("inf")),
        ("max_records", -1),
        ("max_records", True),
        ("max_output_tokens", 9000),
        ("retries", 5),
        ("timeout_seconds", 200),
    ],
)
def test_invalid_generation_limits(field, value):
    with pytest.raises(GenerationError, match=field):
        generate_records([SOURCE], config("http://127.0.0.1:1/v1", **{field: value}))
