"""Loopback-only JSON API and static application server."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .demo import demo_datasets
from .ingest import ValidationError, parse_upload
from .pipeline import default_recipe
from .store import ConflictError, NotFoundError, Store

MAX_BODY_BYTES = 26 * 1024 * 1024
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message
        super().__init__(message)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    store: Store
    static_dir: Path | None
    model_endpoint: str
    demo_lock: threading.Lock

    def server_close(self) -> None:
        super().server_close()
        if hasattr(self, "store"):
            self.store.close()


class Handler(BaseHTTPRequestHandler):
    server: LocalServer
    server_version = "DataCurator/0.1"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, _format: str, *_args: Any) -> None:
        # No imported content, query strings, or inference prompts in access logs.
        return

    def _trusted_authority(self, value: str, *, origin: bool = False) -> bool:
        # urlsplit strips some controls and accepts empty userinfo. Reject those
        # spellings before parsing rather than trusting their normalized host.
        if not value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
            return False
        try:
            parsed = urlsplit(value if origin else "//" + value)
            if (
                parsed.username is not None
                or parsed.password is not None
                or parsed.hostname not in LOOPBACK_NAMES
            ):
                return False
            if origin and (
                parsed.scheme not in {"http", "https"} or parsed.path or parsed.query or parsed.fragment
            ):
                return False
            if not origin and (parsed.path or parsed.query or parsed.fragment):
                return False
            port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
            valid_ports = {self.server.server_port, 5173} if origin else {self.server.server_port}
            return port in valid_ports
        except ValueError:
            return False

    def _check_access(self) -> None:
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1 or not self._trusted_authority(hosts[0]):
            raise HTTPError(403, "Access requires a loopback Host with the server port")
        origins = self.headers.get_all("Origin") or []
        if len(origins) > 1 or (origins and not self._trusted_authority(origins[0], origin=True)):
            raise HTTPError(403, "Remote browser origins are not permitted")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise HTTPError(403, "Cross-site requests are not permitted")

    def _respond(
        self,
        status: int,
        content: bytes,
        content_type: str = "application/json; charset=utf-8",
        *,
        filename: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        origin = self.headers.get("Origin")
        if origin and self._trusted_authority(origin, origin=True):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def _json(self, status: int, value: Any, *, filename: str | None = None) -> None:
        self._respond(
            status,
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"),
            filename=filename,
        )

    def _request_json(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise HTTPError(400, "Chunked or encoded request bodies are not supported")
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1:
            raise HTTPError(400, "Exactly one Content-Length header is required")
        try:
            length = int(lengths[0])
        except ValueError as exc:
            raise HTTPError(400, "Invalid Content-Length") from exc
        if length < 0:
            raise HTTPError(400, "Invalid Content-Length")
        if length > MAX_BODY_BYTES:
            raise HTTPError(413, "Request body exceeds the 26 MiB limit")
        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            raise HTTPError(415, "Content-Type must be application/json")
        content = self.rfile.read(length)
        if len(content) != length:
            raise HTTPError(400, "Incomplete request body")
        try:

            def reject_constant(_value: str) -> None:
                raise ValueError("Non-finite JSON numbers are not permitted")

            value = json.loads(content.decode("utf-8"), parse_constant=reject_constant)
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise HTTPError(400, f"Invalid JSON request: {str(exc)[:160]}") from exc
        if not isinstance(value, dict):
            raise HTTPError(400, "Request JSON must be an object")
        return value

    @staticmethod
    def _keys(body: dict[str, Any], allowed: set[str], required: set[str] | None = None) -> None:
        extra = set(body) - allowed
        missing = (required or set()) - set(body)
        if extra:
            raise HTTPError(400, f"Unknown fields: {', '.join(sorted(extra))}")
        if missing:
            raise HTTPError(400, f"Missing fields: {', '.join(sorted(missing))}")

    @staticmethod
    def _page(query: dict[str, list[str]], *, default: int = 50) -> tuple[int, int]:
        try:
            offset = int(query.get("offset", ["0"])[0])
            limit = int(query.get("limit", [str(default)])[0])
        except (ValueError, IndexError) as exc:
            raise HTTPError(400, "Pagination offset and limit must be integers") from exc
        if offset < 0 or not 1 <= limit <= 500:
            raise HTTPError(400, "Pagination requires offset >= 0 and limit between 1 and 500")
        return offset, limit

    def _route(self) -> None:
        self._check_access()
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if "\x00" in path or "\\" in path:
            raise HTTPError(400, "Invalid request path")
        query = parse_qs(parsed.query, max_num_fields=20)
        parts = path.strip("/").split("/")
        store = self.server.store
        if self.command == "OPTIONS":
            self.send_response(204)
            origin = self.headers.get("Origin")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.command in {"GET", "HEAD"}:
            if path == "/api/health":
                self._json(200, {"status": "ok", "local_only": True, "version": "0.1.0"})
                return
            if path == "/api/recipe/default":
                recipe = default_recipe()
                recipe["create"]["endpoint"] = self.server.model_endpoint
                self._json(200, recipe)
                return
            if path == "/api/datasets":
                self._json(200, {"datasets": store.list_datasets()})
                return
            if len(parts) == 3 and parts[:2] == ["api", "datasets"]:
                dataset = store.get_dataset(parts[2])
                offset, limit = self._page(query)
                dataset["records"] = dataset["records"][offset : offset + limit]
                dataset.update(offset=offset, limit=limit)
                self._json(200, dataset)
                return
            if path == "/api/runs":
                self._json(200, {"runs": store.list_runs()})
                return
            if path == "/api/recipes":
                self._json(200, {"recipes": store.list_recipes()})
                return
            if len(parts) >= 3 and parts[:2] == ["api", "runs"]:
                identifier = parts[2]
                if len(parts) == 3:
                    self._json(200, store.get_run(identifier))
                    return
                if len(parts) == 4 and parts[3] in {"records", "decisions"}:
                    kind = parts[3]
                    records = (
                        store.get_run_records(identifier)
                        if kind == "records"
                        else store.get_run_decisions(identifier)
                    )
                    if kind == "decisions" and "action" in query:
                        action = query["action"][0]
                        if action not in {"keep", "reject", "generate", "select"}:
                            raise HTTPError(400, "Unknown decision action")
                        records = [record for record in records if record.get("action") == action]
                    offset, limit = self._page(query, default=100 if kind == "decisions" else 50)
                    self._json(200, {kind: records[offset : offset + limit], "total": len(records)})
                    return
                artifact_names = {
                    "export": "curated.jsonl",
                    "rejected": "rejected.jsonl",
                    "manifest": "manifest.json",
                    "recipe": "recipe.json",
                    "report": "report.json",
                    "generated": "generated.jsonl",
                }
                if len(parts) == 4 and parts[3] in artifact_names:
                    name = artifact_names[parts[3]]
                    content = store.get_artifact(identifier, name)
                    mime = (
                        "application/x-ndjson; charset=utf-8"
                        if name.endswith(".jsonl")
                        else "application/json; charset=utf-8"
                    )
                    self._respond(200, content, mime, filename=f"{identifier[:8]}-{name}")
                    return
            if path.startswith("/api/") or path == "/api":
                raise NotFoundError("API route not found")
            self._static(path)
            return
        if self.command == "DELETE":
            if len(parts) == 3 and parts[:2] == ["api", "datasets"]:
                store.delete_dataset(parts[2])
                self._json(200, {"deleted": True})
                return
            raise NotFoundError("API route not found")
        if self.command != "POST":
            raise HTTPError(405, "Method not supported")
        body = self._request_json()
        if path == "/api/datasets":
            self._keys(
                body,
                {"name", "role", "filename", "content", "text_field", "kind", "license"},
                {"name", "role", "filename", "content"},
            )
            if not isinstance(body["content"], str):
                raise HTTPError(400, "content must be a UTF-8 string")
            try:
                content = body["content"].encode("utf-8")
            except UnicodeEncodeError as exc:
                raise HTTPError(400, "content must contain valid Unicode") from exc
            records = parse_upload(
                content,
                body["filename"],
                text_field=body.get("text_field", "text"),
                kind=body.get("kind", "text"),
            )
            dataset = store.create_dataset(
                body["name"],
                body["role"],
                records,
                hashlib.sha256(content).hexdigest(),
                license=body.get("license", "unknown"),
                original=content,
                filename=body["filename"],
            )
            self._json(201, {key: value for key, value in dataset.items() if key != "records"})
            return
        if path == "/api/demo":
            self._keys(body, set())
            with self.server.demo_lock:
                datasets = store.list_datasets()
                seeded = []
                for item in demo_datasets():
                    content = item["content"].encode("utf-8")
                    digest = hashlib.sha256(content).hexdigest()
                    existing = next(
                        (
                            dataset
                            for dataset in datasets
                            if dataset["sha256"] == digest
                            and dataset["name"] == item["name"]
                            and dataset["role"] == item["role"]
                        ),
                        None,
                    )
                    if existing:
                        seeded.append(existing)
                        continue
                    records = parse_upload(content, item["filename"], kind=item.get("kind", "text"))
                    dataset = store.create_dataset(
                        item["name"],
                        item["role"],
                        records,
                        digest,
                        license=item.get("license", "MIT"),
                        original=content,
                        filename=item["filename"],
                    )
                    summary = {key: value for key, value in dataset.items() if key != "records"}
                    seeded.append(summary)
                    datasets.append(summary)
            self._json(200, {"datasets": seeded})
            return
        if path == "/api/runs":
            self._keys(body, {"dataset_ids", "recipe"}, {"dataset_ids"})
            recipe = body.get("recipe", {})
            self._check_generation_endpoint(recipe)
            self._json(202, store.start_run(body["dataset_ids"], recipe))
            return
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] in {"cancel", "retry"}:
            self._keys(body, set())
            if parts[3] == "cancel":
                self._json(200, store.cancel_run(parts[2]))
            else:
                self._check_generation_endpoint(store.get_run(parts[2])["recipe"])
                self._json(202, store.retry_run(parts[2]))
            return
        if path == "/api/recipes":
            self._keys(body, {"name", "recipe"}, {"name", "recipe"})
            self._check_generation_endpoint(body["recipe"])
            self._json(201, store.save_recipe(body["name"], body["recipe"]))
            return
        raise NotFoundError("API route not found")

    def _check_generation_endpoint(self, recipe: Any) -> None:
        if not isinstance(recipe, dict):
            raise HTTPError(400, "recipe must be an object")
        create = recipe.get("create", {})
        if not isinstance(create, dict):
            raise HTTPError(400, "recipe.create must be an object")
        endpoint = create.get("endpoint", self.server.model_endpoint)
        if endpoint != self.server.model_endpoint:
            raise HTTPError(
                400,
                "Inference endpoint is configured by the server administrator; use --model-endpoint when starting the server",
            )
        if "endpoint" not in create and self.server.model_endpoint != default_recipe()["create"]["endpoint"]:
            recipe["create"] = {**create, "endpoint": self.server.model_endpoint}

    def _static(self, path: str) -> None:
        root = self.server.static_dir
        if root is None or not root.is_dir():
            raise NotFoundError(
                "Web application is not built. Build web/dist or run the Vite development server"
            )
        if any(part in {"..", "."} for part in path.split("/")):
            raise HTTPError(404, "Static file not found")
        candidate = (root / path.lstrip("/")).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPError(404, "Static file not found")
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            # SPA fallback only for paths without an asset extension.
            if Path(path).suffix:
                raise NotFoundError("Static file not found")
            candidate = root / "index.html"
        candidate = candidate.resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise NotFoundError("Static file not found")
        mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._respond(200, candidate.read_bytes(), mime)

    def _handle(self) -> None:
        try:
            self._route()
        except HTTPError as exc:
            self._json(exc.status, {"error": exc.message})
        except NotFoundError as exc:
            self._json(404, {"error": str(exc)})
        except ConflictError as exc:
            self._json(409, {"error": str(exc)})
        except (ValidationError, ValueError, TypeError) as exc:
            message = str(exc)[:1000]
            status = 413 if "20 MiB" in message or "26 MiB" in message else 400
            self._json(status, {"error": message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:
            self._json(500, {"error": "Internal local server error; no dataset content was transmitted"})

    do_GET = _handle
    do_HEAD = _handle
    do_POST = _handle
    do_DELETE = _handle
    do_OPTIONS = _handle
    do_PUT = _handle
    do_PATCH = _handle


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    data_dir: Path | str = Path(".data-curator"),
    static_dir: Path | str | None = None,
    *,
    model_endpoint: str | None = None,
) -> LocalServer:
    if host not in LOOPBACK_NAMES:
        raise ValueError("Data Curator binds only to localhost, 127.0.0.1, or ::1")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("Port must be an integer between 0 and 65535")
    endpoint = (
        model_endpoint
        or os.environ.get("DATA_CURATOR_MODEL_ENDPOINT")
        or default_recipe()["create"]["endpoint"]
    )
    # The generator also checks the complete URL on every request; configuration
    # must not bypass its transport safeguards.
    from .generation import validate_endpoint

    validate_endpoint(endpoint)
    cls = LocalServer
    if host == "::1":

        class IPv6LocalServer(LocalServer):
            address_family = socket.AF_INET6

        cls = IPv6LocalServer
    server = cls((host, port), Handler)
    try:
        server.store = Store(data_dir)
        server.static_dir = Path(static_dir).expanduser().resolve() if static_dir else None
        server.model_endpoint = endpoint
        server.demo_lock = threading.Lock()
    except Exception:
        server.server_close()
        raise
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local Data Curator API and web application")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(".data-curator"))
    parser.add_argument(
        "--static-dir", type=Path, default=Path(__file__).resolve().parents[2] / "web" / "dist"
    )
    parser.add_argument(
        "--model-endpoint", default=None, help="Administrator-configured local OpenAI-compatible endpoint"
    )
    args = parser.parse_args(argv)
    server = create_server(
        args.host, args.port, args.data_dir, args.static_dir, model_endpoint=args.model_endpoint
    )
    print(f"Data Curator is available at http://{args.host}:{server.server_port} (loopback only)")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
