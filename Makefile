.PHONY: install test lint typecheck check smoke serve task

install:
	uv sync --extra dev

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

task:
	python scripts/taskctl.py show $(ID)
