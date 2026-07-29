# M01: Dataset manifest and robust downloaders

## Objective

Turn the current download scripts into reproducible, integrity-aware dataset acquisition with a machine-readable manifest.

## Context files

- `docs/DATASETS.md`
- `scripts/data/`
- `benchmarks/manifests/datasets.json`
- `tests/test_dataset_manifest.py`


Do not scan the whole repository before planning. Use `rg` to locate any additional symbol.

## Scope

- Add expected file names, source URLs, dataset version/date, license reference, size when available, and optional checksum fields.
- Use atomic temporary downloads and resume-safe behavior.
- Add JSON shape and sample-count checks without loading entire large files into memory when avoidable.
- Print actionable messages for missing optional datasets.


## Non-goals

- Do not normalize benchmark records.
- Do not download LongMemEval Medium in tests.
- Do not vendor any dataset.


## Acceptance criteria

- [ ] Manifest validates against a typed schema.
- [ ] Downloader refuses unexpected HTML/error pages.
- [ ] Existing valid files are not re-downloaded unless --force.
- [ ] Offline tests use local fixtures only.


## Verification

```bash
uv run python scripts/data/verify_datasets.py --allow-missing
uv run pytest -q tests/test_dataset_manifest.py
```

## Codex execution prompt

```text
Execute only task M01. Read AGENTS.md, TASKS.md, and this task file first. Start with a concise plan. Stay inside Scope and Non-goals. Use the listed context files before broader search. Run every verification command. Do not begin any later task. When finished, report changed files, exact command results, acceptance status, and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable credentials, a dataset license decision, or a destructive migration. A missing optional external service must have a deterministic local fake, not block unit tests.
