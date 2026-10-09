"""Boundary tests use real HTTP; imported content is data, never a path or code."""

import json

import pytest
from test_integration import live_api as live_api
from test_integration import permissive_recipe, running_api


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "127.0.0.1.evil.example", "localhost.evil.example", "192.168.1.20"])
def test_untrusted_host_is_rejected(live_api, host):
    status, _, _ = live_api.request("/api/health", headers={"Host": host})
    assert status == 403


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost.evil.example:5173", "http://127.0.0.1:9", "file://", "http://192.168.1.20:5173"])
def test_untrusted_browser_origin_cannot_mutate(live_api, origin):
    status, _, headers = live_api.request("/api/demo", "POST", {}, headers={"Origin": origin})
    assert status == 403
    assert headers.get("Access-Control-Allow-Origin") != "*"
    assert live_api.ok("/api/datasets")["datasets"] == []


def test_loopback_origin_and_actual_host_port_are_allowed(live_api):
    status, _, headers = live_api.request(
        "/api/health", headers={"Origin": live_api.url, "Host": f"127.0.0.1:{live_api.port}"}
    )
    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") != "*"


def test_invalid_json_and_oversized_body_are_rejected_before_import(live_api):
    status, error, _ = live_api.request("/api/datasets", "POST", raw_body=b'{"name":')
    assert status == 400 and error["error"]
    status, _, _ = live_api.request(
        "/api/datasets", "POST", raw_body=b"", headers={"Content-Length": str(100 * 1024 * 1024)}
    )
    assert status == 413
    assert live_api.ok("/api/datasets")["datasets"] == []


def test_record_size_and_record_count_limits_are_atomic(live_api):
    for content in (
        json.dumps({"text": "x" * 200_001}),
        "\n".join(json.dumps({"text": f"record {i}"}) for i in range(10_001)),
    ):
        status, error, _ = live_api.request(
            "/api/datasets", "POST", {"name": "Over limit", "role": "source", "filename": "large.jsonl", "content": content}
        )
        assert status in {400, 413}, error
        assert error["error"]
        assert live_api.ok("/api/datasets")["datasets"] == []


@pytest.mark.parametrize("weight", [-1, 0, float("nan"), float("inf"), -float("inf")])
def test_invalid_mixture_weights_are_rejected_before_run(live_api, weight):
    dataset = live_api.upload("Weight source", [{"text": "This source is long enough for a curation run but the requested mixture is invalid."}])
    recipe = permissive_recipe(live_api)
    recipe["compose"]["weights"] = {dataset["id"]: weight}
    status, error, _ = live_api.request("/api/runs", "POST", {"dataset_ids": [dataset["id"]], "recipe": recipe})
    assert status == 400, error
    assert error["error"]
    assert live_api.ok("/api/runs")["runs"] == []


def test_unknown_recipe_keys_and_foreign_dataset_ids_are_rejected(live_api):
    dataset = live_api.upload("Known source", [{"text": "Configuration validation catches typos instead of silently dropping their meaning."}])
    recipe = permissive_recipe(live_api)
    recipe["calibrate"]["qualitty_min"] = 0.3
    status, error, _ = live_api.request("/api/runs", "POST", {"dataset_ids": [dataset["id"]], "recipe": recipe})
    assert status == 400, error
    status, _, _ = live_api.request("/api/runs", "POST", {"dataset_ids": ["not-a-real-dataset"], "recipe": permissive_recipe(live_api)})
    assert status in {400, 404}
    assert live_api.ok("/api/runs")["runs"] == []


@pytest.mark.parametrize("endpoint", ["https://example.com/v1", "http://169.254.169.254/v1", "file:///etc/passwd", "http://127.0.0.1.evil.example/v1", "http://user:password@127.0.0.1/v1"])
def test_remote_or_credentialed_generation_endpoint_is_rejected(live_api, endpoint):
    dataset = live_api.upload("Private source", [{"text": "Private local records must never be sent to a remote or ambiguous model endpoint."}])
    recipe = permissive_recipe(live_api)
    recipe["create"].update(enabled=True, model="test-local-model", endpoint=endpoint)
    status, error, _ = live_api.request("/api/runs", "POST", {"dataset_ids": [dataset["id"]], "recipe": recipe})
    assert status == 400, error
    assert live_api.ok("/api/runs")["runs"] == []


def test_static_traversal_does_not_disclose_files(tmp_path):
    static = tmp_path / "public"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>Safe application</title>")
    secret = "OUTSIDE_STATIC_ROOT_SECRET_910281"
    (tmp_path / "secret.txt").write_text(secret)
    with running_api(tmp_path / "state", static) as api:
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/%2e%2e%2fsecret.txt", "/..%2fsecret.txt", "/%252e%252e/secret.txt"):
            status, body, _ = api.request(path)
            assert secret.encode() not in (body if isinstance(body, bytes) else json.dumps(body).encode())
            assert status in {400, 403, 404}, (path, status, body)


def test_upload_filename_cannot_write_outside_store_and_markup_is_plain_data(live_api, tmp_path):
    destination = tmp_path / "must-not-exist.py"
    malicious_text = '<img src=x onerror="globalThis.__injected=true"> <script>alert("unsafe")</script>'
    status, result, _ = live_api.request(
        "/api/datasets",
        "POST",
        {
            "name": "<script>dataset name</script>",
            "role": "source",
            "filename": str(destination),
            "kind": "code",
            "content": malicious_text,
        },
    )
    assert status in {200, 201, 400}, result
    assert not destination.exists()
    if status in {200, 201}:
        detail = live_api.ok(f"/api/datasets/{result['id']}")
        assert detail["records"][0]["text"] == malicious_text


@pytest.mark.parametrize("path", ["/api/datasets/..%2f..%2fetc%2fpasswd", "/api/runs/..%2f..%2fetc%2fpasswd/export"])
def test_encoded_ids_are_not_filesystem_paths(live_api, path):
    assert live_api.request(path)[0] in {400, 404}


def test_server_refuses_public_interface(tmp_path):
    from data_curator.server import create_server

    with pytest.raises((ValueError, OSError)):
        create_server(host="0.0.0.0", port=0, data_dir=tmp_path)
