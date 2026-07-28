# Repository instructions

## Mission

Build a framework-independent temporal event-memory service for long-horizon agents. The main research contributions are ETEC (evidence-constrained temporal consolidation) and QEMR (query-adaptive hybrid retrieval). Do not turn this repository into a general coding-agent clone.

## Task protocol

- Work on exactly one task file from `tasks/mainline/` or `tasks/optional/` per chat.
- Read `TASKS.md`, the selected task file, and only the source files needed for that task.
- Start with a short plan. Do not implement future task IDs.
- Keep changes small enough to review in one diff.
- Run every verification command listed in the task file.
- Stop when acceptance criteria pass; report changed files, test results, and unresolved risks.
- Never mark benchmark gains without generated result artifacts.

## Architecture boundaries

- Core memory logic must not depend on OpenCode, Pi, LangGraph, or a specific model vendor.
- Agent adapters call the public service/SDK interfaces; they do not own memory algorithms.
- Preserve evidence provenance and temporal validity on all durable memories.
- Use deterministic programmatic metrics where possible. LLM judges require cached inputs/outputs and a documented judge model.
- Do not commit datasets, secrets, model weights, generated benchmark caches, or private user traces.

## Commands

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

## Code style

- Python 3.11+ with type annotations.
- Prefer small pure functions and explicit ports/interfaces.
- Domain and service layers must not import FastAPI or database clients.
- Add tests for behavior, not implementation details.
- Use UTC-aware datetimes.

## Code review rules

- Reject changes that mix benchmark methods under unequal model, context-budget, or retrieval-budget settings.
- Reject memory records that cannot point back to source evidence.
- Reject silent fallback from temporal/graph retrieval to vector retrieval; fallback must be observable.
- Reject broad refactors not required by the selected task.
