.PHONY: setup dev check test build
setup:
	uv sync --extra dev
	cd web && npm ci
build:
	cd web && npm run build
dev:
	uv run data-curator serve
check:
	uv run ruff check .
	uv run mypy
	cd web && npm run lint && npm run build
test:
	uv run pytest
	cd web && npm run test:e2e
