.PHONY: install test lint typecheck check smoke serve docker-build task

install:
	uv sync --extra dev --extra models --extra postgres --extra bench

test:
	uv run pytest -q

lint:
	uv run ruff check .

typecheck:
	uv run mypy src

check: lint typecheck test smoke

smoke:
	uv run python -m evoeventmem.cli smoke

serve:
	uv run uvicorn evoeventmem.api.app:app --reload

docker-build:
	docker build -t evoeventmem:local .

task:
	python scripts/taskctl.py show $(ID)
