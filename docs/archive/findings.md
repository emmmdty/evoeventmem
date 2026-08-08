# Findings & Decisions

## Requirements

- Deliver a complete remediation specification and implementation plan for OpenCode.
- Use multiple subagents rather than enforcing one task ID per execution.
- Keep logically continuous work together.
- Avoid duplicate repairs and concurrent ownership of shared files.
- Cover the verified M11-M16 gaps: router validation, QEMR temporal retrieval, strict budgets, M13 provider wiring and main run, LoCoMo provenance/structural evaluation, ETEC stress evaluation, M15 analysis integrity, and M16 PostgreSQL/pgvector production readiness.

## Research Findings

- M11 and M12 unit/smoke acceptance passes, but LoCoMo demonstrates that current QEMR temporal routing/fusion degrades exact match materially.
- The temporal source ranks memories by recency to a reference time without conditioning on the query entity/event or temporal operator.
- The M13 live model factory reuses the chat model configuration for embeddings and ignores the configured embedding model; the attempted main run contains only `config.json`.
- The M14 main run is complete over 1,986 unique questions, but official event summaries are both extraction inputs and structural targets.
- LoCoMo predicted evidence is empty for memory methods because summary evidence lacks official raw turn IDs.
- ETEC acts on only 6 of 668 LoCoMo events, so this run does not meaningfully test consolidation behavior.
- M15 is a substantial uncommitted WIP. It has paired bootstrap and generated artifacts, but is LoCoMo-specific, lacks a true evidence ablation, contains non-binding budget ablations, and hard-codes run-specific narrative numbers.
- M16 is also an uncommitted WIP. Local API tests pass while PostgreSQL contract tests skip; the schema does not use pgvector and endpoint scoping can be omitted.
- The current working tree already contains user-owned M15/M16 changes, so the execution plan needs explicit baseline and ownership gates.
- `tasks/index.json` is stale relative to `TASKS.md`; only the Integration Lead may reconcile task metadata after implementation status is verified.
- `docs/CODEX_WORKFLOW.md` normally requires one task ID per session, but the user explicitly approved a dependency-aware multi-subagent exception for this remediation. File ownership and dependency gates replace task-ID isolation for this work.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Retrieval/Method owns router and retrieval core | Router, temporal candidate generation, fusion, packing, and retrieval diagnostics must change coherently |
| Benchmark/Evidence owns shared benchmark/provider/provenance code and both dataset runners | M13 and M14 share model, artifact, evidence, and runner concerns |
| Analysis/Claims owns only `benchmarks/analysis`, its tests, and final reports | Prevents it from patching retrieval or runner behavior to make analyses pass |
| Production owns API, repository, migrations, configuration, metrics, Compose, and their tests | Keeps production changes independent from research algorithm changes |
| Integration Lead owns cross-workstream contracts, merge order, task index/docs, and final verification | A single authority resolves interface drift and evidence gates |
| Start parallel work only after a baseline and interface-freeze gate | Existing uncommitted WIP and shared ports otherwise make worktree branches inconsistent |
| Final claims require a clean Git commit and immutable run artifacts | Dirty-run results cannot support reproducible method claims |
| Vector RAG indexes normalized raw turns; only event-memory methods consume the cached extraction snapshot | Prevents the baseline from receiving event representations and preserves equal reader budgets |
| Workstream A implements ablation switches, B executes them, and C only validates/analyzes finalized artifacts | Removes conflicting ownership and duplicate repairs across method, runner, and report layers |
| Source runs and analysis reports have separate content-addressed finalization records | Analysis cannot mutate or silently replace evidence-producing runs |
| Deliver one tracked master plan plus four tracked continuous workstream plans | OpenCode worktrees inherit complete instructions while subsystem workers avoid task-ID duplication |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| Existing source and test changes are uncommitted | The spec will require a baseline snapshot and forbid destructive cleanup or broad checkout/reset operations |
| Some required experiments need credentials or remote embeddings | Agents prepare and validate exact commands; execution pauses for explicit user approval |
| First independent design review found ambiguous baseline extraction, overlapping ablation ownership, weak non-active-ablation gates, mutable report paths, and a review-sample edge case | Revised the spec with raw-turn-only vector RAG, A/B/C responsibility separation, controlled decision-delta gates, write-once hashes, separate analysis artifacts, and “all if fewer than 50” sampling |
| Second independent review found that the proposed evidence ablation could violate provenance, stop conditions still conflated event/non-event extraction, schema and stress-fixture ownership lacked exact paths, and Gate F targeted the legacy report path | Replaced the ablation with `constrained` versus `provenance_only`, assigned each contract and stress fixture to an exact owner/path, corrected method-comparison invariants, and added content-addressed report generation/validation commands |
| Plan review found untracked-plan inheritance, missing ablation executions, PG/Compose sequencing, stress handoff, ignored datasets, merge commands, production embedding, and compatibility-scope blockers | Added P0/B0/F0 tracking, stable worktrees/data links, controlled/base/dataset ablation lifecycle, early/final DB approvals, explicit cross-branch merges, per-dataset compatibility, frozen async embedding, and restart persistence commands; final reviewer approved the complete set |

## Resources

- `TASKS.md`
- `tasks/mainline/M11_query_router.md` through `tasks/mainline/M16_production_api.md`
- `src/evoeventmem/router.py`
- `src/evoeventmem/retrieval.py`
- `benchmarks/longmemeval/run.py`
- `benchmarks/locomo/run.py`
- `benchmarks/analysis/`
- `src/evoeventmem/api/app.py`
- `src/evoeventmem/infra/`
- `runs/main/report/report.md`

## Visual/Browser Findings

- None; the specification is textual and does not need a visual companion.
