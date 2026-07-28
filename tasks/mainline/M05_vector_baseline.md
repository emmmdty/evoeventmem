# M05: Model gateway and vector RAG baseline

## Objective

Add vendor-neutral chat/embedding ports and a reproducible vector retrieval baseline.

## Context files

- `docs/MODEL_STRATEGY.md`
- `tasks/mainline/M04_context_baselines.md`
- `src/evoeventmem/core/ports.py`
- `benchmarks/`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Implement OpenAI-compatible chat and embedding clients behind ports.
- Provide deterministic fake models for tests.
- Implement chunking, indexing, cosine retrieval, optional reranking port, and context packing.
- Cache model inputs/outputs by content hash for experiments.


## Non-goals

- Do not hardcode an OpenAI model name.
- Do not implement event extraction.
- Do not add a graph database.


## Acceptance criteria

- [ ] Vector baseline runs on fixtures without network.
- [ ] Live provider is enabled only with explicit config and credentials.
- [ ] Retrieval scores and selected context are persisted.


## Verification

```bash
pytest -q tests/models tests/benchmarks/test_vector_baseline.py
```

## Codex execution prompt

```text
Execute only task M05. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
