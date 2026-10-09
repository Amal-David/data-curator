"""Command-line access to the same persisted engine used by the web studio."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .demo import demo_datasets

ARTIFACTS = ("curated.jsonl", "rejected.jsonl", "manifest.json", "recipe.json", "report.json")
MAX_FILE_BYTES = 20 * 1024 * 1024


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))


def _read_file(path: Path, maximum: int = MAX_FILE_BYTES) -> bytes:
    if not path.is_file():
        raise ValueError(f"Input file does not exist or is not a regular file: {path}")
    with path.open("rb") as handle:
        content = handle.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError(f"Input file exceeds the {maximum // (1024 * 1024)} MiB limit: {path.name}")
    return content


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON numeric value {value}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data-curator",
        description="Local-first, inspectable dataset curation. No network is used unless local model generation is enabled.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(".data-curator"),
        help="Persisted workspace directory (default: .data-curator)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        child = subparsers.add_parser(name, help=help_text)
        child.add_argument(
            "--data-dir", type=Path, default=argparse.SUPPRESS, help="Persisted workspace directory"
        )
        return child

    serve = command("serve", "Start the loopback API and web studio")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--static-dir", type=Path, default=Path("web/dist"))
    serve.add_argument("--model-endpoint", help="Administrator-configured literal loopback model endpoint")
    imported = command("import", "Import an immutable local dataset")
    imported.add_argument("path", type=Path)
    imported.add_argument("--name")
    imported.add_argument("--role", choices=("source", "target", "holdout"), default="source")
    imported.add_argument("--kind", choices=("text", "code"), default="text")
    imported.add_argument("--text-field", default="text")
    imported.add_argument(
        "--license", default="unknown", help="Declared source license (no automatic inference)"
    )
    command("datasets", "List imported datasets and their roles")
    command("demo", "Idempotently import the original offline demo datasets")
    run = command("run", "Run curation and wait for the persisted result")
    run.add_argument(
        "--dataset",
        action="append",
        dest="dataset_ids",
        help="Dataset ID to include; repeat for each source/target/holdout (default: all)",
    )
    run.add_argument("--recipe", type=Path, help="Recipe JSON file; omitted fields use documented defaults")
    run.add_argument("--seed", type=int)
    run.add_argument("--max-records", type=int)
    command("runs", "List persisted runs")
    recipe = command("recipe", "Print the default reusable recipe")
    recipe.set_defaults(command="recipe")
    export = command("export", "Export curated/rejected JSONL, recipe, report, and manifest")
    export.add_argument("run_id")
    export.add_argument("--output", type=Path, required=True, help="Destination directory")
    export.add_argument("--overwrite", action="store_true", help="Replace existing artifact files")
    return parser


def _demo(store: Any) -> list[dict[str, Any]]:
    from .ingest import parse_upload

    existing = store.list_datasets()
    imported = []
    for fixture in demo_datasets():
        content = fixture["content"].encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        match = next(
            (
                item
                for item in existing
                if item["name"] == fixture["name"]
                and item["role"] == fixture["role"]
                and item["sha256"] == digest
            ),
            None,
        )
        if match is not None:
            imported.append(match)
            continue
        records = parse_upload(content, fixture["filename"], kind=fixture.get("kind", "text"))
        dataset = store.create_dataset(
            fixture["name"],
            fixture["role"],
            records,
            digest,
            license=fixture["license"],
            original=content,
            filename=fixture["filename"],
        )
        imported.append(dataset)
        existing.append(dataset)
    return imported


def _export(store: Any, run_id: str, output: Path, overwrite: bool) -> dict[str, Any]:
    # Read and validate all artifacts before writing anything to the destination.
    artifacts = {name: store.get_artifact(run_id, name) for name in ARTIFACTS}
    for name in ARTIFACTS:
        path = output / name
        if path.is_symlink() or (path.exists() and not overwrite):
            raise ValueError(f"Export would replace {path}. Choose another directory or use --overwrite.")
    output.mkdir(parents=True, exist_ok=True)
    for name, content in artifacts.items():
        with tempfile.NamedTemporaryFile(dir=output, prefix=".export-", delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(output / name)
        finally:
            temporary.unlink(missing_ok=True)
    return {
        "run_id": run_id,
        "directory": str(output.resolve()),
        "files": [
            {"name": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in artifacts.items()
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "recipe":
            from .pipeline import default_recipe

            _emit(default_recipe())
            return 0
        if args.command == "serve":
            from .server import create_server

            server = create_server(
                host=args.host,
                port=args.port,
                data_dir=args.data_dir,
                static_dir=args.static_dir,
                model_endpoint=args.model_endpoint,
            )
            host, port = server.server_address[:2]
            if isinstance(host, bytes):
                host = host.decode("ascii")
            print(f"Data Curator is available at http://{host}:{port}", file=sys.stderr, flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        from .store import Store

        store = Store(args.data_dir)
        try:
            if args.command == "datasets":
                _emit({"datasets": store.list_datasets()})
            elif args.command == "demo":
                _emit({"datasets": _demo(store)})
            elif args.command == "import":
                from .ingest import parse_upload

                content = _read_file(args.path)
                records = parse_upload(content, args.path.name, text_field=args.text_field, kind=args.kind)
                dataset = store.create_dataset(
                    args.name or args.path.stem,
                    args.role,
                    records,
                    hashlib.sha256(content).hexdigest(),
                    license=args.license,
                    original=content,
                    filename=args.path.name,
                )
                _emit(dataset)
            elif args.command == "runs":
                _emit({"runs": store.list_runs()})
            elif args.command == "run":
                from .pipeline import validate_recipe

                recipe: dict[str, Any] = {}
                if args.recipe:
                    recipe = json.loads(
                        _read_file(args.recipe, 1024 * 1024).decode("utf-8"), parse_constant=_reject_constant
                    )
                    if not isinstance(recipe, dict):
                        raise ValueError("Recipe must be a JSON object.")
                if args.seed is not None:
                    recipe["seed"] = args.seed
                if args.max_records is not None:
                    if not isinstance(recipe.get("compose", {}), dict):
                        raise ValueError("Recipe compose must be a JSON object.")
                    recipe.setdefault("compose", {})["max_records"] = args.max_records
                recipe = validate_recipe(recipe)
                dataset_ids = args.dataset_ids or [item["id"] for item in store.list_datasets()]
                run = store.start_run(dataset_ids, recipe)
                last_stage = None
                try:
                    while run["status"] in ("queued", "running"):
                        progress = run.get("progress") or {}
                        stage = progress.get("stage")
                        if stage and stage != last_stage:
                            print(f"{stage}: {progress.get('message', '')}", file=sys.stderr, flush=True)
                            last_stage = stage
                        time.sleep(0.05)
                        run = store.get_run(run["id"])
                except KeyboardInterrupt:
                    store.cancel_run(run["id"])
                    print(
                        "Cancellation requested. The persisted run records its final status.", file=sys.stderr
                    )
                    return 130
                _emit(run)
                return 0 if run["status"] == "completed" else 1
            elif args.command == "export":
                _emit(_export(store, args.run_id, args.output, args.overwrite))
        finally:
            store.close()
        return 0
    except (ValueError, OSError, LookupError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
