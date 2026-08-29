# M11-M16 Remediation Design

## Purpose

Repair the engineering and research-validity gaps discovered in M11-M16 and
produce clean, reproducible evidence for the EvoEventMem research claims. The
work will be executed by multiple OpenCode subagents organized by coherent
subsystem rather than by milestone ID.

The user explicitly authorizes this remediation to override the repository's
usual one-task-ID-per-session convention. All other repository constraints
remain active: no silent fallback, no untraceable memory, no unequal benchmark
budgets, no generated gain claims without immutable artifacts, and no automatic
GPU or long-running remote jobs.

## Success Definition

The remediation is complete only when all of the following are true:

1. Query routing and QEMR retrieval pass deterministic behavioral tests and no
   longer let unconditional recency dominate temporal queries.
2. Retrieval budgets use a declared tokenizer/estimator and cover the complete
   packed context passed to the reader.
3. LongMemEval Small and LoCoMo use separately configured reader, extractor,
   and embedding models and contain no oracle event/evidence input in the main
   memory comparison.
4. Every durable event used in the main comparison points to raw source turns;
   official LoCoMo event summaries are evaluation targets only.
5. ETEC is evaluated on samples where merge/supersession behavior is actually
   exercised, with results stratified by consolidation action.
6. M15 derives every table, narrative value, confidence interval, and caveat
   from validated artifacts for both datasets; no run-specific number is
   hard-coded in source.
7. The production service uses enforced request scope, an actual PostgreSQL /
   pgvector query path, async connection pooling, stable schema generation,
   observable partial failures, and passing PostgreSQL contract tests.
8. Final benchmark claims are generated from a clean Git commit and immutable
   run artifacts. Existing dirty-run results remain available only as legacy
   diagnostics.

## Non-Goals

- Do not train a learned router or learned fusion model.
- Do not introduce a second graph database or frontend.
- Do not run LongMemEval Medium, optional benchmarks, GPU training, or large
  local inference.
- Do not tune weights on LoCoMo test outcomes and then report the same outcomes
  as unbiased evaluation.
- Do not silently relax evidence validation to improve recall.
- Do not replace the framework-independent domain/service architecture with a
  vendor-specific agent framework.
- Do not delete, reset, stash, or overwrite the user's current uncommitted
  M15/M16 work.

## Execution Topology

### Integration Lead

The Integration Lead owns coordination rather than a feature subsystem. It:

- records the baseline worktree and existing WIP before workers start;
- obtains approval before committing or relocating user-owned WIP;
- freezes shared interfaces used by more than one workstream;
- creates workstream branches/worktrees from the same baseline;
- resolves interface-change requests instead of allowing cross-owned edits;
- merges in dependency order and runs the repository-wide gates;
- owns task-status metadata and final documentation;
- presents exact remote/live commands for approval but never starts them
  automatically.

### Workstream A: Memory Method

Owns deterministic query understanding, retrieval, consolidation behavior, and
their method-level tests.

Responsibilities:

- router evaluation and confidence diagnostics;
- temporal constraint parsing;
- query-conditioned temporal candidate scoring;
- robust source fusion and observable fallback;
- evidence-aware, tokenizer-backed budget packing;
- ETEC behavior fixes revealed by the predeclared stress suite.

### Workstream B: Benchmark and Evidence

Owns shared model construction, benchmark runners, raw-turn extraction,
provenance mapping, experiment and ablation configurations/execution,
experiment manifests, and clean source-run artifact generation.

Responsibilities:

- one shared provider factory for separate reader/extractor/embedding models;
- LongMemEval and LoCoMo raw-turn extraction with exact evidence spans;
- official LoCoMo `dia_id` mapping from normalized raw turns;
- structural evaluation that never consumes its gold target as extractor input;
- clean, resumable smoke and main run manifests;
- execution and finalization of controlled and dataset ablation runs using
  Workstream A's public switches;
- consolidation stress harness and action-stratified benchmark outputs.

### Workstream C: Analysis and Claims

Owns artifact validation, statistical analysis of finalized ablation runs,
failure taxonomy, reports, and generated analysis artifacts. It may not execute
source runs or change method/runner behavior to make a report pass.

Responsibilities:

- dataset-neutral run loading and compatibility checks;
- paired statistics with declared primary comparisons;
- factor-isolation validation and statistical analysis of evidence, temporal,
  graph, router, weight, and binding-budget ablation artifacts produced by B;
- dynamic claim text and tables for LongMemEval and LoCoMo;
- deterministic failure sampling plus a human-review handoff sheet;
- refusal to publish from missing, incompatible, dirty, or stale artifacts.

### Workstream D: Production Service

Owns API, persistence, pgvector integration, request scope, configuration,
observability, deployment, and production-facing tests.

Responsibilities:

- async PostgreSQL connection pooling and versioned migrations;
- pgvector-backed candidate search under tenant/user/session scope;
- async API application service that reuses pure domain rules;
- required request scope and scope/body consistency checks;
- health/readiness, request IDs, bounded-cardinality metrics, timeouts, and
  explicit runtime failure/fallback events;
- PostgreSQL repository contracts and Docker Compose smoke validation.

## Exclusive File Ownership

The Integration Lead enforces this table. A worker that needs another owner's
file must submit an interface request and continue on non-blocked work.

| Owner | Files and directories |
|---|---|
| Integration Lead | `AGENTS.md`, `TASKS.md`, `tasks/index.json`, `docs/ARCHITECTURE.md`, `docs/EVALUATION.md`, `docs/CODEX_WORKFLOW.md`, `pyproject.toml`, new `docs/contracts/m11_m16_remediation.md` contract registry, final integration notes |
| Workstream A | `src/evoeventmem/router.py`, `src/evoeventmem/retrieval.py`, `src/evoeventmem/consolidation.py`, new `src/evoeventmem/tokenization.py`, `tests/retrieval/`, `tests/consolidation/`, `tests/fixtures/consolidation/`, `benchmarks/retrieval_smoke.py`, retrieval fixtures, method-side ablation controls and contract tests |
| Workstream B | `benchmarks/common/`, `benchmarks/longmemeval/`, `benchmarks/locomo/`, new `benchmarks/experiments/` including `benchmarks/experiments/etec_stress.py` and `benchmarks/experiments/fixtures/etec_stress_v1.json`, `configs/longmemeval/`, `configs/locomo/`, new `configs/ablations/`, `src/evoeventmem/extraction.py`, `tests/extraction/`, benchmark tests including new `tests/benchmarks/test_etec_stress.py`, dataset-specific benchmark fixtures, ablation execution and raw run artifacts |
| Workstream C | `benchmarks/analysis/`, `tests/analysis/`, new `configs/analysis/`, generated `artifacts/analysis/<analysis_id>/` outputs; it may read finalized source runs but never write below a source run directory |
| Workstream D | `src/evoeventmem/api/`, `src/evoeventmem/infra/`, `src/evoeventmem/services/memory_service.py`, production-only service modules, `src/evoeventmem/core/ports.py`, `tests/api/`, `tests/infra/`, `tests/services/` when production rules require them, `Dockerfile`, `docker-compose.yml`, `scripts/compose_smoke.py`, `scripts/generate_openapi.py`, `api/openapi.json` |

Two special rules prevent accidental overlap:

1. Workstream A owns method switches and their semantics, Workstream B owns
   ablation configurations and execution, and Workstream C only validates and
   analyzes finalized ablation outputs. No worker reimplements another layer.
2. Workstream B consumes Workstream A's public retrieval models and never edits
   retrieval internals.
3. Workstream D may extend `core/ports.py`, but it must preserve the synchronous
   `MemoryRepository` contract consumed by research code. New async production
   ports are additive until the final integration review.
4. A frozen contract is edited only by the feature owner named below, after
   Integration Lead approval. The lead edits only the contract registry and
   cross-cutting dependencies in `pyproject.toml`; it never patches another
   owner's schema implementation.
5. Workstream B owns the predeclared benchmark stress inputs and expected case
   IDs in `benchmarks/experiments/fixtures/etec_stress_v1.json`. Workstream A
   treats those inputs as read-only and owns only unit/regression fixtures under
   `tests/fixtures/consolidation/` created from reproduced failures.

## Phase 0: Baseline and Interface Freeze

No feature worker starts before this phase passes.

### Baseline gate

The Integration Lead records:

```bash
git status --short
git diff --stat
git diff -- Dockerfile docker-compose.yml pyproject.toml \
  src/evoeventmem/api/app.py src/evoeventmem/core/ports.py \
  src/evoeventmem/infra/in_memory_repository.py \
  src/evoeventmem/services/memory_service.py
```

It inventories all untracked M15/M16 files and runs the existing full test
suite. It must not reset, clean, or stash. If the WIP is to become the shared
baseline, the user approves one explicit baseline commit containing only the
reviewed M15/M16 files. Otherwise workers operate sequentially in the current
tree and parallelize only read-only work.

### Shared interface freeze

The Integration Lead records these owner-implemented contracts in
`docs/contracts/m11_m16_remediation.md` before workstreams diverge:

- Workstream A owns `TokenEstimator` in `src/evoeventmem/tokenization.py`; it
  counts text and complete reader messages for a declared model/tokenizer
  version.
- Workstream A owns retrieval request/result contracts in
  `src/evoeventmem/retrieval.py`, including intent, temporal constraints,
  component scores, exclusions, packed items, provenance, and budget counts.
- Workstream B owns `RunManifest`, `AblationRunManifest`, finalization records,
  and per-question prediction/retrieval/evidence/consolidation/summary schemas
  in `benchmarks/common/artifacts.py`.
- Workstream D owns `RequestScope` and additive async production repository
  ports in `src/evoeventmem/core/ports.py`.

Each owner supplies a focused contract test and consumer-impact note. The
Integration Lead approves the version and updates only the registry; then the
feature owner implements any approved contract change. These schemas and tests
must be frozen before feature branches diverge.

## Workstream A Design: Router, QEMR, Budget, and ETEC

### Router validation

The existing 21-case fixture remains a unit fixture, not a generalization
claim. Add a separate evaluation fixture sourced from benchmark-style queries
and annotate both the expected intent and temporal operator. Report confusion
matrix, per-label F1, Macro-F1, abstention/fallback rate, and confidence bins.
The fixture must include ambiguous and adversarial negatives; a test must fail
if the evaluation set is identical to the rule-development fixture.

### Temporal query model

Introduce an explicit deterministic temporal constraint with these operators:

- `none`;
- `at` / date range;
- `before`;
- `after`;
- `between`;
- `earliest` / first;
- `latest` / most recent;
- `sequence`;
- `duration`.

Parsing records matched spans, normalized UTC bounds, rule hits, and an
observable reason. Queries such as "When did Caroline move?" have a temporal
answer type but no recency constraint; they must not automatically favor the
latest unrelated memory.

### Candidate generation and fusion

Temporal retrieval becomes two-stage:

1. Dense/entity/relation evidence establishes query relevance.
2. Temporal constraints filter or rerank relevant candidates.

For explicit dates, before/after/between constraints score interval agreement.
For earliest/latest, temporal order is applied only within the relevant
candidate set. For unconstrained `when` questions, temporal presence is a
small feature while dense/entity relevance remains dominant.

Replace per-source max normalization as the sole fusion mechanism because it
promotes the best candidate from an irrelevant source to 1.0. Use deterministic
weighted reciprocal-rank fusion, retain raw/component ranks, and keep the fixed
vector baseline unchanged. Weight profiles are declared before full benchmark
runs and validated on synthetic/held-out temporal cases, never tuned on the
reported LoCoMo test outcomes.

Any source failure returns an explicit exclusion/fallback event. Dense failure
may fall back to the remaining configured sources only when the result records
the missing source and degraded policy; no silent QEMR-to-vector substitution is
allowed.

### Strict budget semantics

Packing receives the frozen `TokenEstimator`. The budget includes evidence
labels, separators, metadata rendered into context, the question, reader
directive, and chat-message overhead—not only `memory.content.split()`.
Results record `content_tokens`, `prompt_overhead_tokens`, and
`total_input_tokens_estimate`. Packing must reserve overhead before selecting
items and must never exceed the configured estimate.

The retrieval policy exposes an `evidence_policy` with `constrained` and
`provenance_only` settings for the predeclared M15 ablation. Both settings
require every durable and packed memory to retain non-empty source evidence.
`constrained` applies evidence-based eligibility, coverage, and scoring;
`provenance_only` disables only those additional decision effects while keeping
provenance mandatory and immutable. A provenance-free case may exist only in a
synthetic, non-durable negative test that never reaches a reader, source-run
artifact, or publication claim.

Budget tests cover Unicode, punctuation, metadata, and a case where item count
does not bind before token budget. `max_items_per_source` remains a diversity
guardrail rather than an accidental substitute for budget enforcement.

### ETEC stress behavior

Workstream A does not change consolidation thresholds merely because LoCoMo
shows no gain. It first consumes Workstream B's predeclared stress cases:

- exact duplicate;
- paraphrase merge;
- newer fact supersedes older fact;
- stale incoming fact remains historical;
- unrelated same-entity events remain separate;
- overlapping and non-overlapping validity intervals;
- conflicting evidence and missing evidence;
- cross-tenant/user/session isolation.

Only reproducible behavioral failures authorize changes to
`consolidation.py`. Every change receives a regression test and preserves
evidence lineage and UTC temporal semantics.

## Workstream B Design: Providers, Provenance, and Experiments

### Shared provider factory

Move runner-specific live-model creation behind one benchmark provider
factory. Reader, extractor, and embedding configurations are separate resolved
objects with independent endpoint, API-key environment name, model ID,
timeout, and optional thinking mode.

The M13 embedding client must use `embedding_model`, never `chat_model`.
Config validation resolves and prints effective model IDs without printing
secrets. Unit tests use fakes/mocks to prove each client receives the correct
model and endpoint without making network calls.

### Raw-turn event extraction

The `vector_rag` baseline indexes normalized raw-turn chunks directly. It never
uses extracted events, event summaries, generated observations, QA evidence, or
answers. Event-memory methods (`event_no_etec`, `etec`, and `full`) build their
extraction input from the same normalized raw turns after all target and answer
fields are cleared. Each accepted event must contain at least one exact,
validated raw-turn span carrying the original raw turn ID.

Smoke runs use a deterministic fake extractor. Main event-memory methods share
one declared cached extraction snapshot per conversation; extraction occurs
once and is reused by those methods. `vector_rag` receives only its raw-turn
chunks and never consumes that snapshot. Every reader method receives its own
retrieved context under the same complete reader-input token budget and the
same declared reader model/tokenizer. Construction cost and model calls are
reported separately from per-query retrieval/reader cost.

Invalid or unsupported extraction evidence produces a cached rejection record;
it is never silently converted to summary provenance.

### LoCoMo evidence and structure

Normalized raw turns preserve official `dia_id` as `raw_turn_id`. Predicted
evidence comes only from packed raw-turn references and is compared with
official QA evidence IDs.

Official `event_summary` is used only as an evaluation target. Structural
metrics compare independently extracted events to official summaries per
session and record matching policy, threshold, counts, precision, coverage,
and F1. The report labels this as a structural proxy, not extraction accuracy.

### LongMemEval and LoCoMo manifests

Both runners emit the same resolved manifest fields:

- dataset path and hash;
- complete method set;
- reader, extractor, and embedding provider/model/version;
- tokenizer/estimator name and version;
- extraction, router, retrieval, and consolidation policy versions;
- input budget and candidate/item caps;
- Git commit and dirty status;
- code-tree or dirty-diff hash for diagnostic runs;
- sample/question scope and expected IDs.

Resume refuses manifest drift. Summary generation detects missing and duplicate
sample/question IDs and validates required derived artifacts.

### Clean experiment gates

The workflow distinguishes three artifact classes:

- `smoke`: deterministic fake, tiny fixture, never used for method claims;
- `diagnostic`: live or full data allowed on a dirty tree, used only to debug;
- `publication`: full declared scope, clean Git tree, immutable config, complete
  cache, and all validation checks passing.

Existing M14 results are retained and labeled diagnostic. They are not
overwritten or silently promoted.

A completed source run is sealed by a write-once `FINALIZED.json` containing
the manifest hash, hashes for every required artifact, completion counts, and
finalization time. Publication loaders refuse missing hashes, hash drift, or
any attempted overwrite after finalization. Diagnostic legacy paths such as
`runs/main/report/` remain historical inputs only and are never treated as the
new analysis output contract.

Analysis writes to `artifacts/analysis/<analysis_id>/`, where `analysis_id` is
derived from the finalized source-run hashes plus the analysis-config hash.
The analysis artifact has its own `FINALIZED.json`; rerunning identical inputs
must validate or fail rather than mutate either the analysis directory or its
source runs.

Before any live or remote command, the worker stops and presents:

- exact command;
- exact working directory;
- required environment variables by name only;
- whether `ssh gpu-4090` or another approved remote endpoint is needed;
- expected output directory and files;
- expected duration/cost class;
- resume and failure-recovery command.

The command runs only after explicit user approval.

## Workstream C Design: Analysis and Claims

### Artifact validation

Validation must reject:

- zero discovered runs when a report is requested;
- schema mismatch or unknown schema;
- config or dataset hash mismatch;
- missing/duplicate samples or questions;
- missing predictions, samples, retrieval traces, or required caches;
- incompatible reader, extractor, embedding, tokenizer, budget, caps, method
  set, dataset, or evaluation policy;
- publication claims from dirty/subset/diagnostic runs;
- claims referring to run IDs or config hashes not present in the validated
  input set.

LongMemEval and LoCoMo loaders normalize into one analysis row schema without
forcing dataset-specific methods such as `session_summary` onto LongMemEval.

### Statistics and claims

Primary comparisons are declared in configuration before analysis. Confidence
intervals use paired per-question bootstrap. If multiple primary hypotheses are
tested, adjusted p-values use Holm correction while raw p-values remain
available. Claims distinguish descriptive association from causal improvement.

Every narrative value is formatted from a structured result object. Source
must contain no run-specific metric literal. A golden test changes synthetic
input values and verifies that tables, claims, plots, and prose all change
together.

### Required ablations

Workstream A implements and tests the method-side controls. Workstream B owns
the configurations, executes the controlled and dataset runs, and finalizes
their artifacts. Workstream C consumes those artifacts, validates the declared
factor isolation, computes statistics, and renders reports.

Each ablation records the exact changed factor while holding reader, extractor,
embedding, data, and other budgets constant:

- `evidence_policy=provenance_only` versus `constrained`, with source
  provenance mandatory in both variants;
- temporal source removed;
- graph source removed;
- rule router versus forced/fallback routing;
- fixed vector, fixed hybrid, and QEMR weights;
- budgets selected so at least two settings materially bind packing.

Every required switch must first be active on a deterministic controlled
fixture: at least one selection, exclusion, routing, ranking, consolidation, or
packing decision must differ from its paired control. If not, Gate D fails; a
non-active dataset result cannot substitute for this fixture. On a publication
dataset, a switch may legitimately produce no row-level difference, but it is
then reported as no observed dataset effect and cannot support an effect claim.
Budget experiments raise the item cap enough for token budget to become the
limiting factor and must bind at least one publication question at two or more
budget settings. Offline proxy results are labeled retrieval diagnostics and
are not presented as end-to-end QA gains.

### Error taxonomy and review sheet

Extend the taxonomy to separate:

- extraction/provenance rejection;
- router classification/fallback;
- candidate-generation miss;
- temporal filtering/ranking error;
- evidence-constraint exclusion;
- budget truncation;
- answer absent from packed context;
- answer present but reader wrong;
- adversarial/no-answer protocol cases.

Generate a deterministic stratified sample of at least 50 failures, or all
failures if fewer than 50 exist, across datasets, methods, categories, and
failure types. The review sheet includes
blank `reviewer_label`, `reviewer_comment`, and `reviewed_at` fields. Automatic
labels are hypotheses until reviewed; final claims state review coverage.

## Workstream D Design: Production API and Persistence

### Async persistence architecture

Preserve the synchronous `MemoryRepository` used by research/domain code. Add
an async, scope-aware production port and an async PostgreSQL implementation
backed by an `asyncpg` pool. Remove the dedicated event-loop thread and shared
single connection from the production path.

Extract collision, feedback, forget, and scope rules into small pure functions
used by both the existing synchronous service and a new async API application
service. This avoids duplicating business behavior while keeping database I/O
async.

### pgvector path

Versioned migrations enable the vector extension and persist embeddings with
model ID and dimension metadata. One deployment supports one configured indexed
embedding dimension; readiness fails on model/dimension/schema mismatch.
Search performs a tenant/user/session-filtered pgvector query and returns its
source, score decomposition, and fallback state. Python token-overlap search may
remain only as an explicitly named development baseline.

The migration and index strategy must be idempotent and tested against the
Compose PostgreSQL image. No generated embedding, secret, or raw text appears
in default logs.

### Enforced scope

API requests receive a `RequestScope` from required tenant/user headers or an
equivalent authenticated dependency; optional session narrows the scope.
Body/query identity must match request scope. Explain, feedback, forget, and
search never accept an unscoped UUID lookup. Cross-tenant/user/session tests
must return not-found or forbidden without revealing record existence.

This is isolation, not a full authentication system; OAuth/RBAC remains out of
scope.

### Failure and fallback semantics

Production PostgreSQL mode fails closed by default. If the configured store is
unavailable, readiness is 503 and writes do not succeed into an invisible
volatile store. An explicitly configured development-only volatile fallback
may serve requests only when responses, logs, metrics, and health state all
identify degradation.

Database timeouts, embedding failures, partial retrieval-source failures, and
fallbacks emit structured events with request ID, stable reason code, source,
and duration. Metrics use route templates rather than UUID-bearing raw paths
and record exception responses as well as successes.

### Deployment verification

Repository contracts run against both in-memory and actual PostgreSQL without
counting skipped tests as success. OpenAPI generation is deterministic and the
artifact is tracked. Compose smoke exercises migration, readiness, scoped
write, pgvector search, explain, feedback, forget, metrics, restart persistence,
and a negative cross-scope request.

## Dependency Graph and Concurrency

```text
Phase 0 baseline + shared contracts
        |
        +--> Workstream A method fixes --------+
        |                                      |
        +--> Workstream B provider/provenance --+--> clean smoke/main artifacts
        |                                      |             |
        +--> Workstream C framework fixes -----+             +--> final M15 reports
        |
        +--> Workstream D production service ----------------> production integration
                                                               |
all merged + clean artifacts + report validation --------------> release gate
```

Allowed parallelism:

- A and B start after Phase 0 and work against frozen contracts.
- C may repair loaders, validators, dynamic rendering, and synthetic tests in
  parallel, but final report generation waits for A+B publication artifacts.
- D may proceed independently after the scope/port contract freezes.
- B's live experiments wait for A's retrieval policy version to freeze.
- A's ETEC code changes wait for B's stress cases to reproduce a failure.

## Subagent Coordination Protocol

Every worker prompt contains:

1. its objective and owned paths;
2. forbidden paths owned by other workers;
3. frozen interfaces and upstream commit IDs;
4. required tests and artifact schemas;
5. a reminder that other workers are active and their edits must not be
   reverted;
6. a stop condition for any cross-owned change, credential, GPU job, destructive
   migration, or experimental-policy choice;
7. required handoff: commits, exact commands/results, changed schemas, risks,
   and interface requests.

Workers do not merge, rewrite task status, generate final claims, or run remote
jobs. The Integration Lead reviews each branch against the ownership table
before merging.

## Merge and Validation Gates

### Gate A: Method correctness

- focused router/retrieval/consolidation tests pass;
- temporal regression demonstrates that unrelated recency no longer wins;
- packed prompt estimate is within budget;
- fallback/exclusion reasons are observable;
- every declared ETEC stress case ID is present and passes;
- action-stratified results are emitted, and both merge and supersession paths
  execute at least once rather than remaining zero-count theory branches.

### Gate B: Benchmark validity

- both LongMemEval and LoCoMo smoke runs complete;
- `vector_rag` indexes raw-turn chunks only, while event methods reuse the
  declared event snapshot without exposing it to the vector baseline;
- model factory tests prove independent reader/extractor/embedding wiring;
- every packed main-memory item has raw-turn provenance;
- structural target is absent from extraction input;
- manifests and resume validation pass.

### Gate C: Publication artifacts

- user approves exact live/remote commands;
- runs execute from a clean commit;
- expected sample/question IDs are complete and unique;
- all method/config/budget compatibility checks pass;
- immutable artifacts and cache metadata exist;
- validated, finalized publication artifacts exist for both LongMemEval Small
  and LoCoMo before any two-dataset headline claim is generated.

### Gate D: Analysis validity

- every required ablation changes at least one controlled-fixture decision;
- publication-dataset no-effect results are labeled as such and never counted
  as proof that an ablation executed correctly;
- both LongMemEval Small and LoCoMo finalized rows are validated and analyzed;
- budget settings bind actual publication questions at two or more settings;
- headline numbers independently recompute from per-question rows;
- every claim links to validated run IDs and config hashes;
- no hard-coded run metric exists in source;
- source run directories remain hash-verified and read-only while analysis
  writes only to its content-addressed artifact directory;
- human review coverage is documented.

### Gate E: Production readiness

- in-memory and PostgreSQL contract tests execute and pass with zero unexpected
  skips;
- pgvector query, scope isolation, timeouts, and failure observability pass;
- generated OpenAPI equals the tracked artifact;
- `docker compose config` and Compose smoke pass.

### Gate F: Repository-wide verification

Run after all merges:

```bash
uv sync --extra dev --extra models --extra postgres --extra bench
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
uv run python -m benchmarks.retrieval_smoke
uv run python -m benchmarks.longmemeval.run --config configs/longmemeval/smoke.toml
uv run python -m benchmarks.locomo.run --config configs/locomo/smoke.toml
uv run python -m benchmarks.analysis.report \
  --config configs/analysis/main.toml \
  --source-run runs/publication/longmemeval \
  --source-run runs/publication/locomo \
  --output-root artifacts/analysis
uv run python -m benchmarks.analysis.validate_report \
  --config configs/analysis/main.toml \
  --source-run runs/publication/longmemeval \
  --source-run runs/publication/locomo \
  --artifact-root artifacts/analysis
docker compose config
docker compose --profile smoke up --build --abort-on-container-exit --exit-code-from smoke
```

Both analysis commands derive the same `analysis_id` from the two source-run
finalization hashes and the analysis-config hash. The validator must locate
that exact directory, verify its `FINALIZED.json`, and reject legacy
`runs/main/report/` output as a substitute.

The final command creates local containers/volumes and therefore runs only when
the user authorizes that integration action. Publication main runs remain a
separate explicit approval gate.

## Deliverables

- repaired source and tests from all four workstreams;
- shared resolved run-manifest schema;
- independent router evaluation fixture and report;
- raw-turn-based LongMemEval and LoCoMo smoke artifacts;
- user-approved clean main artifacts for both datasets;
- ETEC action-stratified stress results;
- content-addressed, dynamic two-dataset M15 report and review sheet that do not
  modify finalized source runs;
- versioned PostgreSQL/pgvector migrations and tracked OpenAPI schema;
- Compose smoke evidence;
- reconciled `TASKS.md` and `tasks/index.json` statuses based only on passed
  gates;
- a final unresolved-risk register.

## Stop Conditions

Stop and report rather than guessing when:

- the existing dirty worktree cannot be separated from the intended baseline;
- a frozen shared interface must change;
- dataset annotations could leak QA answers/evidence into a method input;
- model credentials or a remote endpoint are unavailable;
- a GPU, long-running, costly, or destructive command is required;
- a migration would alter non-test data;
- fewer than all required publication samples/questions are complete;
- event-memory methods do not share the identical finalized extraction
  snapshot, or any non-event baseline consumes extractor output;
- compared methods do not use the same normalized raw-turn corpus, declared
  reader/model/tokenizer, complete reader-input budget, applicable embedding
  model, and evaluation policy; retrieval/budget differences are allowed only
  when they are the single predeclared experimental factor;
- an apparent gain lacks immutable artifacts or survives only post-hoc tuning.
