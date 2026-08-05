# Workstream B: Benchmark and Evidence Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce fair, resumable, provenance-valid LongMemEval and LoCoMo
runners, controlled ablation executions, ETEC stress evidence, and immutable
source-run artifacts.

**Architecture:** Centralize independent reader/extractor/embedding providers,
normalize raw turns once, build a raw-turn vector corpus separately, and create
one cached extraction snapshot for all event methods. Shared artifact contracts
seal source runs; B executes A-owned controls but never analyzes claims.

**Tech Stack:** Python 3.11+, uv, Pydantic v2, pytest, TOML/JSON/JSONL,
OpenAI-compatible adapters behind cached ports.

---

## Owned Files

**Create:**

- `benchmarks/common/providers.py`
- `benchmarks/common/memory_inputs.py`
- `benchmarks/experiments/__init__.py`
- `benchmarks/experiments/etec_stress.py`
- `benchmarks/experiments/fixtures/etec_stress_v1.json`
- `benchmarks/experiments/ablation.py`
- `configs/ablations/controlled.toml`
- `configs/ablations/longmemeval.toml`
- `configs/ablations/locomo.toml`
- `tests/benchmarks/test_artifact_contract.py`
- `tests/benchmarks/test_artifacts.py`
- `tests/benchmarks/test_providers.py`
- `tests/benchmarks/test_memory_inputs.py`
- `tests/benchmarks/test_etec_stress.py`
- `tests/benchmarks/test_ablation_execution.py`

**Modify:**

- `benchmarks/common/artifacts.py`
- `benchmarks/common/normalization.py` only for lossless raw-turn identity
- `benchmarks/longmemeval/run.py`
- `benchmarks/locomo/run.py`
- `configs/longmemeval/smoke.toml`
- `configs/longmemeval/main.toml`
- `configs/locomo/smoke.toml`
- `configs/locomo/main.toml`
- `src/evoeventmem/extraction.py`
- `tests/extraction/test_event_extraction.py`
- `tests/benchmarks/test_longmemeval_run.py`
- `tests/benchmarks/test_locomo_run.py`

**Forbidden:** A method internals, `benchmarks/analysis/`, production code,
lead-owned docs/metadata/`pyproject.toml`, and C analysis artifacts.

**Phase rule:** the contract-only agent executes B1 before `F0`. The persistent
feature worker starts from `F0`, verifies B1's tests, and begins at B2; it does
not reimplement or recommit B1.

### Task B1: Freeze Artifact, Manifest, and Finalization Contracts

**Files:**

- Modify: `benchmarks/common/artifacts.py`
- Create: `tests/benchmarks/test_artifact_contract.py`
- Create: `tests/benchmarks/test_artifacts.py`

- [ ] **Step 1: Add failing canonical artifact tests**

Require typed `RunManifest`, `AblationRunManifest`, `ExtractionSnapshot`,
`ExtractionRejection`, retrieval/evidence/consolidation records, and
`FinalizationRecord`. A manifest resolves dataset hash/scope; separate reader,
extractor, and embedding identities/endpoints; tokenizer; policies; budgets;
Git state; and config hash.

- [ ] **Step 2: Add failing immutability/hash tests**

Test canonical JSON hashing, required-file enumeration, write-once
`FINALIZED.json`, hash drift rejection, finalized overwrite rejection, resume
manifest drift, missing/duplicate sample/question IDs, and dirty publication
refusal.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/benchmarks/test_artifact_contract.py \
  tests/benchmarks/test_artifacts.py -x
```

Expected: required models and finalization functions are absent.

- [ ] **Step 4: Implement the contract and working/finalized state machine**

Working runs may add write-once per-sample files and regenerate derived files.
After finalization every required path is hashed and no mutation is allowed.
`smoke`, `diagnostic`, and `publication` are distinct classes; only clean,
complete publication runs can finalize as publication evidence.

- [ ] **Step 5: Verify and commit contract-only work**

```bash
uv run pytest -q tests/benchmarks/test_artifact_contract.py \
  tests/benchmarks/test_artifacts.py
git add benchmarks/common/artifacts.py \
  tests/benchmarks/test_artifact_contract.py \
  tests/benchmarks/test_artifacts.py
git commit -m "feat(benchmarks): freeze immutable artifact contracts"
```

Handoff this commit to the Lead for `F0`; C consumes this exact contract.

### Task B2: Centralize Independent Model Providers

**Files:**

- Create: `benchmarks/common/providers.py`
- Create: `tests/benchmarks/test_providers.py`
- Modify: both main/smoke TOML config families
- Later consume from both runner files; do not edit runners in this task

- [ ] **Step 1: Add failing provider separation tests**

Require a resolved bundle with distinct reader, extractor, and embedding
provider/model/base URL/API-key env/timeout/thinking fields. Assert embedding
never falls back to chat model and secrets are never serialized.

```python
def test_embedding_never_falls_back_to_reader_model() -> None:
    with pytest.raises(ValueError, match="embedding model"):
        resolve_models(config_missing_embedding_model)
```

- [ ] **Step 2: Verify RED**

```bash
uv run pytest -q tests/benchmarks/test_providers.py -x
```

Expected: runner-local factories reuse chat configuration and no extractor
configuration exists.

- [ ] **Step 3: Implement the shared factory**

Return a bundle such as:

```python
class ModelBundle(BaseModel):
    resolved: ResolvedModelConfig
    reader: ChatModel
    extractor: ChatModel
    embedding: EmbeddingModel
```

Runtime clients remain wrapped in the existing file caches. Deterministic fake
construction makes zero network calls.

- [ ] **Step 4: Update configs and validate only**

Add explicit reader/extractor/embedding sections to all four configs. Main
config validation prints redacted resolved values; it must not contact a model.
Do not guess whether `gpu-4090` or the existing `gpu-5090` comment is correct.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/benchmarks/test_providers.py
git add benchmarks/common/providers.py tests/benchmarks/test_providers.py \
  configs/longmemeval configs/locomo
git commit -m "refactor(benchmarks): separate reader extractor and embeddings"
```

### Task B3: Build Raw-Turn Inputs and One Extraction Snapshot

**Files:**

- Create: `benchmarks/common/memory_inputs.py`
- Create: `tests/benchmarks/test_memory_inputs.py`
- Modify: `src/evoeventmem/extraction.py`
- Modify: `tests/extraction/test_event_extraction.py`
- Modify: `benchmarks/common/normalization.py` only if raw ID loss is reproduced

- [ ] **Step 1: Add raw vector corpus tests**

Require one normalized memory chunk per eligible raw turn with exact full-turn
evidence and original raw turn ID. Assert chunks contain no event summary,
observation, answer, or QA evidence target.

- [ ] **Step 2: Add extraction leakage/provenance tests**

Require target-cleared extraction input, one extraction invocation per
conversation, exact supporting turn spans, cached rejection for unsupported
spans, and identical snapshot hash for `event_no_etec`, `etec`, and `full`.
Assert `vector_rag` never receives the snapshot.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/extraction/test_event_extraction.py \
  tests/benchmarks/test_memory_inputs.py -x
```

Expected: current rule extractor prefers summaries; vector/event stores share
the wrong representation; raw/ETEC stores extract twice.

- [ ] **Step 4: Implement normalized construction helpers**

Expose three separate operations:

```python
build_raw_turn_corpus(record) -> RawTurnCorpus
extract_event_snapshot(record, extractor) -> ExtractionSnapshot
materialize_event_store(snapshot, *, apply_etec: bool) -> MemoryRepository
```

Every accepted event requires an exact turn span. Summary-only provenance is
invalid. Official summary remains available to evaluation code, not extraction.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/extraction/test_event_extraction.py \
  tests/benchmarks/test_memory_inputs.py \
  tests/benchmarks/test_normalization.py
git add benchmarks/common/memory_inputs.py \
  benchmarks/common/normalization.py src/evoeventmem/extraction.py \
  tests/benchmarks/test_memory_inputs.py \
  tests/extraction/test_event_extraction.py
git commit -m "fix(benchmarks): separate raw corpus from event extraction"
```

### Task B4: Add the Predeclared ETEC Stress Harness

**Files:**

- Create: `benchmarks/experiments/__init__.py`
- Create: `benchmarks/experiments/etec_stress.py`
- Create: `benchmarks/experiments/fixtures/etec_stress_v1.json`
- Create: `tests/benchmarks/test_etec_stress.py`

- [ ] **Step 1: Write all stable stress cases**

Include exact duplicate, paraphrase merge, newer supersedes older, stale input
remains historical, unrelated same-entity events remain separate, overlapping
and disjoint intervals, conflicting/missing evidence, and cross-tenant/user/
session isolation. Every case has a stable ID and expected action/invariants.

- [ ] **Step 2: Add failing harness tests**

Require every expected ID exactly once, one trace per case, action-stratified
summary, nonzero MERGE and SUPERSEDE, UTC-aware intervals, provenance lineage,
and scope isolation.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/benchmarks/test_etec_stress.py -x
```

Expected: harness/fixture imports are absent.

- [ ] **Step 4: Implement runner without changing consolidation**

B invokes the public consolidator and records decisions; B must not edit
thresholds or `consolidation.py`.

- [ ] **Step 5: Run and commit the formal A handoff**

```bash
uv run pytest -q tests/benchmarks/test_etec_stress.py
uv run python -m benchmarks.experiments.etec_stress \
  --fixture benchmarks/experiments/fixtures/etec_stress_v1.json \
  --output-root artifacts/smoke/etec-stress
git add benchmarks/experiments tests/benchmarks/test_etec_stress.py
git commit -m "test(etec): add action-stratified stress suite"
```

Send A the commit SHA, fixture hash, summary, decisions, action counts, and
failing case IDs. Generated smoke artifacts are not committed.

### Task B5: Repair the LongMemEval Runner

**Dependencies:** merged A retrieval/budget policy commit and B1-B3.

**Files:**

- Modify: `benchmarks/longmemeval/run.py`
- Modify: `tests/benchmarks/test_longmemeval_run.py`
- Modify: `configs/longmemeval/smoke.toml`
- Modify: `configs/longmemeval/main.toml`

- [ ] **Step 1: Add failing fairness tests**

Require `vector_rag` raw-turn input, one event snapshot shared by all event
methods, no snapshot for vector, independent model IDs, same reader/estimator/
complete input budget across methods, raw-turn provenance on every packed event,
and separate construction versus per-query cost.

- [ ] **Step 2: Add manifest/resume tests**

Require resolved manifest and extraction snapshot IDs, expected IDs, immutable
per-sample files, manifest-drift refusal, and smoke finalization.
Add an explicit `--run-dir` CLI option for a stable publication directory;
reject simultaneous `--run-dir`, `--resume-dir`, and `--output-root` overrides.
Test that a first run and an identical resume address the same exact directory.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/benchmarks/test_longmemeval_run.py -x
```

Expected: current runner shares extracted store with vector, extracts twice,
and miswires the embedding model.

- [ ] **Step 4: Implement the runner through shared B/A contracts**

Delete runner-local provider and memory-construction duplication. Do not
reconstruct an alternate prompt or switch implementation.

- [ ] **Step 5: Verify smoke and commit**

```bash
uv run pytest -q tests/benchmarks/test_longmemeval_run.py
uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/smoke.toml \
  --output-root artifacts/smoke/longmemeval
git add benchmarks/longmemeval/run.py \
  tests/benchmarks/test_longmemeval_run.py configs/longmemeval
git commit -m "fix(longmemeval): enforce fair raw and event inputs"
```

### Task B6: Repair the LoCoMo Runner and Structural Evaluation

**Files:**

- Modify: `benchmarks/locomo/run.py`
- Modify: `tests/benchmarks/test_locomo_run.py`
- Modify: `configs/locomo/smoke.toml`
- Modify: `configs/locomo/main.toml`

- [ ] **Step 1: Reverse the oracle-leakage tests**

Remove the expectation that extracted events carry summary provenance. Require
official event summaries to be absent from extraction input and used only as a
structural target. Require raw-turn `dia_id` mapping for predicted evidence.

- [ ] **Step 2: Add representation/fairness tests**

Require raw-dialogue vector chunks, one shared event snapshot, independent
provider identities, complete reader budget parity, and no generated
observation/answer/evidence target input.
Require the same stable `--run-dir`/resume CLI contract as LongMemEval.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/benchmarks/test_locomo_run.py -x
```

Expected: current `_locomo_extraction_input()` retains event summaries and old
tests encode summary provenance.

- [ ] **Step 4: Implement independent evidence/structure paths**

Predicted evidence comes only from packed turn refs with official raw IDs.
Structural precision/coverage/F1 compares independently extracted events to
official summaries by declared session-level matching policy and labels the
metric a structural proxy.

- [ ] **Step 5: Verify smoke and commit**

```bash
uv run pytest -q tests/benchmarks/test_locomo_run.py
uv run python -m benchmarks.locomo.run \
  --config configs/locomo/smoke.toml \
  --output-root artifacts/smoke/locomo
git add benchmarks/locomo/run.py tests/benchmarks/test_locomo_run.py \
  configs/locomo
git commit -m "fix(locomo): remove oracle extraction inputs"
```

If deterministic fake scores change, update assertions to verify protocol and
provenance. Never reintroduce gold summaries to preserve an old smoke score.

### Task B7: Execute A-Owned Controls Without Reimplementing Them

**Files:**

- Create: `benchmarks/experiments/ablation.py`
- Create: `configs/ablations/controlled.toml`
- Create: `configs/ablations/longmemeval.toml`
- Create: `configs/ablations/locomo.toml`
- Create: `tests/benchmarks/test_ablation_execution.py`

- [ ] **Step 1: Add failing factor-isolation tests**

For evidence, temporal, graph, router, weights, and budget pairs, require
exactly one manifest factor difference, stable base hashes, and at least one
controlled decision delta. Evidence remains nonempty in both evidence modes.

- [ ] **Step 2: Add binding-budget tests**

Use high item caps and budgets chosen so at least two settings bind packing on
controlled data. Dataset configs must later record question-level
`packing_bound` rather than infer it from item count.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/benchmarks/test_ablation_execution.py -x
```

Expected: no B-owned execution layer or ablation manifest exists.

- [ ] **Step 4: Implement config-to-controls execution**

Instantiate A's public controls. Do not import/override private weights or
duplicate routing/retrieval code. Emit paired raw finalized artifacts; do not
compute claims, bootstrap statistics, or taxonomy.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/benchmarks/test_ablation_execution.py
uv run python -m benchmarks.experiments.ablation \
  --config configs/ablations/controlled.toml \
  --run-dir artifacts/smoke/ablations/controlled
git add benchmarks/experiments/ablation.py configs/ablations \
  tests/benchmarks/test_ablation_execution.py
git commit -m "feat(experiments): execute declared retrieval controls"
```

Expected: controlled output finalizes as smoke/diagnostic evidence and every
required factor has a nonzero decision delta. Generated output is not committed
or handed off as the final proof; the Lead regenerates it from clean `R0` at
`runs/validation/controlled-ablations` and records that finalization hash.

### Task B8: Complete Gate B and Prepare Publication Commands

- [ ] **Step 1: Run the complete deterministic B suite**

```bash
uv run pytest -q tests/extraction tests/benchmarks
uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/smoke.toml \
  --output-root artifacts/smoke/longmemeval
uv run python -m benchmarks.locomo.run \
  --config configs/locomo/smoke.toml \
  --output-root artifacts/smoke/locomo
uv run python -m benchmarks.experiments.etec_stress \
  --fixture benchmarks/experiments/fixtures/etec_stress_v1.json \
  --output-root artifacts/smoke/etec-stress
uv run ruff check benchmarks src/evoeventmem/extraction.py \
  tests/benchmarks tests/extraction
uv run mypy src
```

Expected: both smokes finalize; vector manifests say `input_kind=raw_turn`;
event methods share one snapshot ID; stress cases/actions are complete.

- [ ] **Step 2: Validate main configs without network**

```bash
uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/main.toml --validate-config
uv run python -m benchmarks.locomo.run \
  --config configs/locomo/main.toml --validate-config
uv run python -m benchmarks.experiments.ablation \
  --config configs/ablations/longmemeval.toml --validate-config
uv run python -m benchmarks.experiments.ablation \
  --config configs/ablations/locomo.toml --validate-config
```

At this stage ablation validation is static: schema, factor matrix, expected A
policy version, and controlled-run requirement. Base-run hash/isolation
validation occurs only after the Lead creates each finalized base run.

The dataset executor must require `--controlled-run`. Every dataset
`AblationRunManifest` embeds the controlled `FINALIZED.json` hash and refuses a
missing, inactive, or hash-drifted controlled run. C receives the same path/hash.

Expected: redacted independent providers and a clean/frozen run manifest.

- [ ] **Step 3: Prepare, but do not run, approval packets**

Provide separate packets for the two base runs and the two dataset ablation
runs. Include exact cwd, command, env names, models/endpoints, config/base-run
hash, changed-factor matrix, expected cache/model calls, clean commit, output
files, duration/cost, and resume command. Flag the unresolved `gpu-4090` versus
`gpu-5090` endpoint conflict to the Lead/user. Do not assume base-run approval
also approves ablations.

- [ ] **Step 4: Commit any final runner-only validation fixes**

```bash
git status --short
git add benchmarks/common benchmarks/longmemeval benchmarks/locomo \
  benchmarks/experiments configs tests/benchmarks \
  tests/extraction src/evoeventmem/extraction.py
git commit -m "test(benchmarks): enforce publication artifact gates"
```

Do not stage generated runs/caches or foreign files.

## Workstream B Handoff

Report:

- base `F0` and B commit SHAs;
- artifact/provider schema versions and consumer notes;
- exact changed paths and tests;
- ETEC fixture hash, all case IDs, traces, action counts, failing IDs;
- A retrieval policy version consumed;
- smoke run IDs/config hashes/finalization hashes;
- controlled ablation decision deltas;
- proposed main commands and approval packets;
- unresolved endpoint/credential/cost risks;
- confirmation that no live/remote/GPU/Docker operation or final claim ran.
