# O09: ETEC mechanism evaluation + 500-sample consistency

## Objective

Close the 9/10 review gaps for the EvoEventMem resume evaluation:

- (a) Produce reproducible conflict-update / temporal-validity mechanism metrics
  (stale-memory error rate, SUPERSEDE/merge decision quality, temporal interval
  exclusion hit rate) with/without-ETEC comparison on LongMemEval
  knowledge-update and temporal-reasoning categories, same reader, budget,
  prompt.
- (b) Run the >=500-sample LongMemEval consistency validation.
- (c) Produce the "why no end-to-end gain" mechanism report and update
  README/EVALUATION/STRONG_RESULTS/INTERVIEW_KIT with traceable numbers.

The full pre-registered experimental design is
`docs/superpowers/specs/2026-08-17-o09-mechanism-eval-and-500-consistency-design.md`
(including the orchestrator's approval addendum). This task file is the
execution contract; the spec is the methodology contract.

## Context files

- `docs/superpowers/specs/2026-08-17-o09-mechanism-eval-and-500-consistency-design.md`
- `docs/METHODOLOGY_CHANGE.md`
- `docs/STRONG_RESULTS_SMALL_SAMPLE.md`
- `docs/EVALUATION.md`
- `benchmarks/experiments/ablation.py` (offline replay precedent)
- `benchmarks/analysis/taxonomy.py` (failure attribution labels)
- `configs/longmemeval/test20-ms.selection.json` (seed mechanism precedent)

Do not scan the whole repository before planning. Use `rg` to locate any
additional symbol.

## Scope

- New mechanism evaluation code under `benchmarks/mechanism/` (gold pairs,
  offline replay, Eval A metrics, stale judge, probes, content-addressed
  mechanism report) with regression tests.
- New runs: mechanism-40 slice (24 KU + 16 TR, frozen selection), Eval B probe
  arms (4), full 500-sample LongMemEval run (6 methods), consistency check,
  M15 report on the 500 run.
- Gold pair annotation for 32 KU questions (8 from ms slice + 24 new) in
  `runs/mechanism/gold/`.
- Conditional pre-registered gap closure (Phase 3B): if the R1 root cause
  (extraction never emits fact_slot/fact_value) is confirmed, implement a
  minimal, prompt-versioned fact metadata emission fix and rerun the mechanism
  slice to verify SUPERSEDE reachability, reporting before/after honestly.
- Document updates: README, docs/EVALUATION.md, docs/STRONG_RESULTS_SMALL_SAMPLE.md,
  docs/INTERVIEW_KIT.md, and `docs/9of10_ACCEPTANCE.md` final report.

## Non-goals

- No modification of existing `src/evoeventmem/` or benchmark runner behavior
  to manufacture trigger counts; no threshold/weight/prompt tuning to chase
  numbers.
- No benchmark-table pollution with synthetic probes (probes stay under
  `runs/mechanism/evalb/`).
- No significance claims for the 500-sample run (power analysis already shows
  expected null).
- No LoCoMo extension, no two-dataset headline, no M7 false-premise evaluation,
  no stale-memory judge without cached inputs/outputs and a fixed judge model.

## Acceptance criteria

- [ ] Eval A + Eval B mechanism metrics tables (M1/M2/M3/M4/M5) produced for
      the KU+TR slices with root-cause diagnosis; every number traceable to
      finalized artifacts or the content-addressed mechanism report.
- [ ] SUPERSEDE/MERGE/REJECT trigger rates on real data reported with root-cause
      bucketing (R1-R7); the controlled-fixture counterfactual (same code,
      explicit fact metadata) reported; Phase 3B gap-closure before/after (if
      triggered) reported honestly with prompt version recorded.
- [ ] 500-sample LongMemEval run finalized (L0 target, or L2/L3 declared
      degradation ladder per spec §6.3); consistency checklist 5 items passed
      with evidence paths.
- [ ] Content-addressed M15 report for the 500 run (analysis config committed to
      repo to avoid the a0907e94 config-loss issue).
- [ ] `docs/9of10_ACCEPTANCE.md` written with a/b/c evidence, numbers, artifact
      paths, three independent acceptance reviews verbatim, and remaining risks.
- [ ] README/EVALUATION/STRONG_RESULTS/INTERVIEW_KIT updated with only
      measured, traceable numbers.

## Verification

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

## Codex execution prompt

```text
Execute only task O09. Read AGENTS.md, TASKS.md, this task file, and the
pre-registered spec first. Start with a concise plan. Stay inside Scope and
Non-goals. Follow the spec gates (C0-C9) in order. Run every verification
command. Never modify sealed runs/ artifacts or change method budgets/prompts.
When finished, report changed files, exact command results, acceptance status,
and remaining risks.
```

## Stop conditions

Stop and report rather than guessing if the task requires unavailable
credentials, the quota ladder L2/L3 decision, or a dataset license decision. A
missing optional external service must have a deterministic local fake, not
block unit tests.