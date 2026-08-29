# Repository Instructions

## Mission

Build a framework-independent temporal event-memory service for long-horizon agents. The two research contributions are:

- **ETEC** — Evidence-constrained temporal consolidation (ADD / MERGE / SUPERSEDE / REJECT).
- **QEMR** — Query-adaptive hybrid retrieval (vector + temporal + graph, dynamically weighted).

This repository is **not** a general coding-agent. Do not add agent planning, tool-use, or LLM orchestration logic here.

## Project layout

```text
src/evoeventmem/        Core Python package (domain, services, infra, api)
benchmarks/             Benchmark adapters, runners, and analysis
adapters/               Agent runtime adapters (OpenCode MCP, Pi)
configs/                TOML configs for benchmark runs and deployment
tasks/mainline/         Ordered mainline task files (M00–M18)
tasks/optional/         Optional extension task files (O01–O09)
docs/                   Architecture, evaluation, and research docs
docs/archive/           Historical docs (superseded, do not edit)
.agents/skills/         Repo-level agent skills
```

## Task protocol

- Work on **exactly one** task file from `tasks/mainline/` or `tasks/optional/` per session.
- Read `TASKS.md`, the selected task file, and only the source files needed.
- Start with a short plan. Do not implement future task IDs.
- Keep changes small enough to review in one diff.
- Run **every** verification command listed in the task file.
- Stop when acceptance criteria pass; report changed files, test results, and unresolved risks.
- Never mark benchmark gains without generated result artifacts.
- **Any task estimated to take >1 hour must get explicit user confirmation before starting.** Always run on the smallest possible sample first (5→10→50), confirm results are sensible, then ask user before scaling up.

## Architecture boundaries

- Core memory logic must **not** depend on OpenCode, Pi, LangGraph, or any model vendor.
- Agent adapters call public service/SDK interfaces; they do not own memory algorithms.
- Preserve evidence provenance and temporal validity on all durable memories.
- Use deterministic programmatic metrics where possible. LLM judges require cached inputs/outputs and a documented judge model.
- Do not commit datasets, secrets, model weights, generated benchmark caches, or private user traces.

## Commands

```bash
# Environment setup
uv sync --extra dev

# Tests
uv run pytest -q

# Lint
uv run ruff check .

# Type check
uv run mypy src

# Smoke test
uv run python -m evoeventmem.cli smoke

# Dev server
uv run uvicorn evoeventmem.api.app:app --reload
```

## Code style

- Python 3.11+ with full type annotations.
- Prefer small pure functions and explicit ports/interfaces.
- Domain and service layers must **not** import FastAPI or database clients.
- Add tests for behavior, not implementation details.
- Use UTC-aware datetimes (`datetime.now(timezone.utc)`).
- Follow existing patterns in neighboring files before introducing new libraries.

## Code review rules

Reject changes that:

- Mix benchmark methods under unequal model, context-budget, or retrieval-budget settings.
- Create memory records that cannot point back to source evidence.
- Silently fall back from temporal/graph retrieval to vector retrieval (fallback must be observable).
- Perform broad refactors not required by the selected task.
- Introduce vendor-specific dependencies in core layers.

## Documentation conventions

- Keep `docs/` for active project documentation only.
- Move superseded docs to `docs/archive/` with `git mv`.
- Benchmark claims must cite artifact paths (`runs/publication/...`).
- All numeric claims require source: config hash, git commit, or artifact filename.

## Server environment

- **Server**: `gpu-5090` via cpolar SSH tunnel (Host=gpu-5090 in ~/.ssh/config)
- **Shared data dir**: `/mnt/aidata/tongjiakai/` and `/home/tongjiakai/` — do NOT write to system disk
- **Embedding server**: `/home/tongjiakai/embed-venv-311/bin/python` runs qwen3-embedding-0.6b on port 11436
  - Start: `ssh gpu-5090 "nohup /home/tongjiakai/embed-venv-311/bin/python /mnt/aidata/tongjiakai/embed_server/qwen_embed_server.py --model-dir /mnt/aidata/tongjiakai/embed_server/qwen3-embedding-0.6b --port 11436 > /tmp/embed_server.log 2>&1 &"`
  - Port forward: `ssh -f -N -L 11436:127.0.0.1:11436 gpu-5090`
  - The venv-311 avoids triton/gcc issues present in embed-venv (3.12)
- **SSH tunnel refresh**: run `cpolar-ssh-update` locally if connection drops
