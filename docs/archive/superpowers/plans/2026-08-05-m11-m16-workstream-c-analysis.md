# Workstream C: Analysis and Claims Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn finalized LongMemEval and LoCoMo source runs into validated,
content-addressed, statistically defensible analysis without running methods,
mutating source artifacts, or hard-coding results.

**Architecture:** Load B-owned manifests/finalization records into one
dataset-neutral row schema, validate compatibility and factor isolation,
compute paired/Holm-adjusted statistics, then render every table/plot/claim from
structured results. Analysis outputs have their own content-derived ID and
write-once finalization record.

**Tech Stack:** Python 3.11+, uv, Pydantic v2, pytest, deterministic bootstrap,
TOML/JSON/JSONL/CSV/SVG/Markdown.

---

## Owned Files

**Create:**

- `configs/analysis/main.toml`
- `benchmarks/analysis/models.py`
- `benchmarks/analysis/loaders.py`
- `benchmarks/analysis/finalization.py`
- `tests/analysis/test_artifact_contract.py`
- `tests/analysis/test_loaders.py`
- `tests/analysis/test_finalization.py`
- `tests/analysis/test_ablation_analysis.py`
- `tests/analysis/test_claims.py`

**Modify:**

- `benchmarks/analysis/__init__.py`
- `benchmarks/analysis/bootstrap.py`
- `benchmarks/analysis/validate_report.py`
- `benchmarks/analysis/ablation.py`
- `benchmarks/analysis/taxonomy.py`
- `benchmarks/analysis/report.py`
- `benchmarks/analysis/svg.py` only when structured rendering requires it
- existing `tests/analysis/` fixtures/tests

**Read only:** B source runs and B artifact models.

**Forbidden:** A/B/D code/configs, all source-run directories, legacy
`runs/main/report/` as authoritative input, task metadata, and `pyproject.toml`.

**Phase rule:** the contract-only C agent executes C1 after B's producer
contract merges and before `F0`. The persistent C worker starts from `F0`,
verifies C1, and begins at C2; it does not reimplement or recommit C1.

### Task C1: Freeze the B-Artifact Consumer Contract

**Dependency:** B artifact contract merged into `F0`.

**Files:**

- Create: `tests/analysis/test_artifact_contract.py`
- Create: `benchmarks/analysis/models.py`

- [ ] **Step 1: Add failing consumer compatibility tests**

Import B's `RunManifest`, `AblationRunManifest`, and finalization models. Build
minimal LongMemEval and LoCoMo payloads and validate them without redefining
their producer fields.

- [ ] **Step 2: Add the dataset-neutral row model**

Require identifiers, dataset/method/category, predictions/answers, QA/evidence
metrics, budget/binding fields, retrieval/fallback/exclusion data,
consolidation actions, model/policy/config/run hashes, and artifact locations.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/analysis/test_artifact_contract.py -x
```

Expected: C consumer models/tests do not yet exist.

- [ ] **Step 4: Implement the consumer layer only**

Do not invent producer defaults or write files. Record any missing producer
field as an interface request to B/Lead.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/analysis/test_artifact_contract.py
git add benchmarks/analysis/models.py tests/analysis/test_artifact_contract.py
git commit -m "test(analysis): freeze benchmark artifact consumers"
```

### Task C2: Load and Validate Both Datasets

**Files:**

- Create: `benchmarks/analysis/loaders.py`
- Create: `tests/analysis/test_loaders.py`
- Modify: `tests/analysis/conftest.py`
- Modify: `benchmarks/analysis/validate_report.py`
- Modify: `tests/analysis/test_validate_report.py`

- [ ] **Step 1: Build synthetic finalized fixtures for both datasets**

Create B-schema-valid synthetic trees. LongMemEval has six methods; LoCoMo has
seven including `session_summary`. Preserve dataset-specific categories while
normalizing common columns.

- [ ] **Step 2: Add failing rejection tests**

Reject zero source runs, unknown schema, missing/hash-drifted finalization,
dirty/diagnostic/subset publication input, missing predictions/samples/
retrieval/caches, missing/duplicate IDs, config/dataset hash drift, and
incompatible reader/extractor/embedding/tokenizer/budget/policy settings.
Do not require the two datasets to have identical method sets.

Compatibility is enforced within each dataset's method comparison and within
each paired ablation family. LongMemEval and LoCoMo may intentionally use
different resolved model stacks and method/category sets; the combined report
must present separate dataset results and never run a cross-dataset paired test
or reject the report merely because those stacks differ.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/analysis/test_loaders.py \
  tests/analysis/test_validate_report.py -x
```

Expected: old validator is LoCoMo-only, treats zero runs as valid, and writes
inside the run root.

- [ ] **Step 4: Implement read-only loaders and validators**

Load methods from each manifest. Never inject `session_summary` into
LongMemEval. Validation returns structured issues and does not write below a
source run.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/analysis/test_artifact_contract.py \
  tests/analysis/test_loaders.py tests/analysis/test_validate_report.py
git add benchmarks/analysis/loaders.py \
  benchmarks/analysis/validate_report.py tests/analysis
git commit -m "feat(analysis): load finalized dataset-neutral runs"
```

### Task C3: Make Analysis Outputs Content-Addressed and Immutable

**Files:**

- Create: `benchmarks/analysis/finalization.py`
- Create: `tests/analysis/test_finalization.py`
- Create: `configs/analysis/main.toml`

- [ ] **Step 1: Add failing `analysis_id` tests**

Require:

```text
analysis_id = sha256(
    sorted base-run, controlled-run, and ablation-run FINALIZED hashes
    + analysis config hash
)
```

Same inputs produce the same ID. A changed source/config changes it. Missing
source finalization, hash drift, or legacy report input fails.

- [ ] **Step 2: Add source immutability tests**

Snapshot every source path/hash/mtime before report generation and assert they
remain unchanged. Rerunning identical analysis must validate or fail, never
mutate. Analysis `FINALIZED.json` hashes every required output.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/analysis/test_finalization.py -x
```

Expected: old report writes `runs_root/report` and no analysis finalizer exists.

- [ ] **Step 4: Implement write-once analysis artifacts**

Write only to `artifacts/analysis/<analysis_id>/`. The config declares primary
comparisons, metrics, bootstrap parameters, alpha/Holm family, and review
sampling settings; it contains no result values.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/analysis/test_finalization.py
git add benchmarks/analysis/finalization.py \
  tests/analysis/test_finalization.py configs/analysis/main.toml
git commit -m "feat(analysis): content-address finalized reports"
```

### Task C4: Add Paired Statistics and Holm Correction

**Files:**

- Modify: `benchmarks/analysis/bootstrap.py`
- Modify: `tests/analysis/test_bootstrap.py`

- [ ] **Step 1: Add failing pairing tests**

Reject missing/duplicate/unmatched question IDs. Primary comparisons must be
declared in config and present in the applicable dataset.

- [ ] **Step 2: Add failing Holm tests**

Test raw and adjusted p-values, deterministic ordering/ties, monotonic adjusted
values, `[0,1]` bounds, and a one-hypothesis family.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/analysis/test_bootstrap.py -x
```

Expected: paired CI exists but no ID alignment API or Holm implementation.

- [ ] **Step 4: Implement small pure functions**

Keep seeded paired bootstrap. Add factor-independent Holm correction and a
structured comparison result retaining raw p, adjusted p, CI, estimate, IDs,
seed, and bootstrap count.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/analysis/test_bootstrap.py
git add benchmarks/analysis/bootstrap.py tests/analysis/test_bootstrap.py
git commit -m "feat(analysis): add paired Holm-adjusted statistics"
```

### Task C5: Analyze, but Never Execute, Ablations

**Dependencies:** A control version and B controlled/finalized ablation
artifacts.

**Files:**

- Modify: `benchmarks/analysis/ablation.py`
- Create: `tests/analysis/test_ablation_analysis.py`

- [ ] **Step 1: Add failing factor-isolation tests**

Cover evidence, temporal, graph, router, weights, and budget families. Paired
manifests may differ in exactly the declared factor. Reader, extractor,
embedding, dataset, budgets except the tested budget, caps, and policies remain
fixed.

- [ ] **Step 2: Add activity/effect tests**

Every required switch has `decision_delta_count > 0` on B's controlled fixture.
A publication dataset with zero row delta is labeled
`no_observed_dataset_effect`, not implementation failure or positive effect.
Budget experiments require two or more settings with actual publication
questions marked `packing_bound=true`. Offline proxies cannot be QA gains.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/analysis/test_ablation_analysis.py -x
```

Expected: current `ablation.py` reruns extraction/consolidation/retrieval and
lacks manifest isolation.

- [ ] **Step 4: Replace execution with artifact-only analysis**

Remove imports of method/extraction/consolidation internals. Read B's finalized
rows and emit structured factor results only.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/analysis/test_ablation_analysis.py
git add benchmarks/analysis/ablation.py \
  tests/analysis/test_ablation_analysis.py
git commit -m "feat(analysis): validate finalized ablation effects"
```

### Task C6: Expand the Typed Failure Taxonomy and Review Sheet

**Files:**

- Modify: `benchmarks/analysis/taxonomy.py`
- Modify: `tests/analysis/test_taxonomy.py`

- [ ] **Step 1: Add exact taxonomy tests**

Cover extraction/provenance rejection, router/fallback, candidate miss,
temporal ranking/filter, evidence exclusion, budget truncation, answer absent
from packed context, answer present but reader wrong, and adversarial/no-answer.

- [ ] **Step 2: Add stratified sampling tests**

Require deterministic coverage across dataset, method, category, and failure
type. Select at least 50, or all failures if fewer. Add blank
`reviewer_label`, `reviewer_comment`, and `reviewed_at`; automatic labels are
explicit hypotheses. Compute review coverage separately.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/analysis/test_taxonomy.py -x
```

Expected: current enum is coarser and review fields/stratification are absent.

- [ ] **Step 4: Implement deterministic trace-based classification**

Use normalized rows and retrieval/extraction traces. Do not call an LLM judge.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/analysis/test_taxonomy.py
git add benchmarks/analysis/taxonomy.py tests/analysis/test_taxonomy.py
git commit -m "feat(analysis): generate stratified failure review handoff"
```

### Task C7: Render Dynamic Two-Dataset Claims

**Files:**

- Modify: `benchmarks/analysis/report.py`
- Modify: `benchmarks/analysis/svg.py` if required
- Create: `tests/analysis/test_claims.py`
- Modify: `tests/analysis/test_report.py`

- [ ] **Step 1: Add the golden anti-hard-code test**

Generate a report, change synthetic metric values/config hashes, regenerate in
a different analysis artifact, and assert JSON, CSV, SVG, Markdown tables, and
prose all change consistently. Scan source to reject run-specific numeric
literals and `runs/main/report` output.

- [ ] **Step 2: Add claim provenance tests**

Every claim has dataset, comparison ID, run IDs, config hashes, metric,
estimate/CI/p-values where applicable, and a status/caveat. Reject a two-dataset
headline when either finalized dataset is missing.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/analysis/test_claims.py \
  tests/analysis/test_report.py -x
```

Expected: current code is LoCoMo-specific, fixed-method, mutable-output, and
contains hard-coded narrative values.

- [ ] **Step 4: Render only from structured results**

Methods/categories are manifest-driven. Narrative templates may express
conditions but contain no run metric. Distinguish descriptive, significant,
no-observed-effect, and retrieval-diagnostic statements.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/analysis/test_claims.py \
  tests/analysis/test_report.py
git add benchmarks/analysis/report.py benchmarks/analysis/svg.py \
  tests/analysis/test_claims.py tests/analysis/test_report.py
git commit -m "feat(analysis): render dynamic two-dataset claims"
```

### Task C8: Implement and Validate the Final CLI Contract

**Files:**

- Modify: `benchmarks/analysis/report.py`
- Modify: `benchmarks/analysis/validate_report.py`
- Modify: `benchmarks/analysis/__init__.py`
- Modify: relevant analysis tests

- [ ] **Step 1: Add CLI tests with synthetic source runs**

Require both commands to derive the same `analysis_id`; report writes below
the output root; validator locates and verifies exactly that artifact. Missing
or legacy sources return nonzero with stable error codes.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest -q tests/analysis -k 'cli or analysis_id or legacy' -x
```

Expected: old positional `runs_root` CLI cannot satisfy the new contract.

- [ ] **Step 3: Implement the exact command surface**

```bash
uv run python -m benchmarks.analysis.report \
  --config configs/analysis/main.toml \
  --source-run runs/publication/longmemeval \
  --source-run runs/publication/locomo \
  --controlled-run runs/validation/controlled-ablations \
  --ablation-run runs/publication/ablations/longmemeval \
  --ablation-run runs/publication/ablations/locomo \
  --output-root artifacts/analysis
uv run python -m benchmarks.analysis.validate_report \
  --config configs/analysis/main.toml \
  --source-run runs/publication/longmemeval \
  --source-run runs/publication/locomo \
  --controlled-run runs/validation/controlled-ablations \
  --ablation-run runs/publication/ablations/longmemeval \
  --ablation-run runs/publication/ablations/locomo \
  --artifact-root artifacts/analysis
```

- [ ] **Step 4: Verify the complete synthetic C gate**

```bash
uv run pytest -q tests/analysis
uv run ruff check benchmarks/analysis tests/analysis
uv run mypy src
```

Expected: all synthetic tests pass. If real source runs do not yet exist, the
two real CLI commands must refuse cleanly; never fabricate them.

- [ ] **Step 5: Commit the framework**

```bash
git add benchmarks/analysis tests/analysis configs/analysis
git commit -m "feat(analysis): finalize content-addressed report workflow"
```

### Task C9: Generate the Final Report After Gate C

**Dependency:** finalized, clean LongMemEval Small and LoCoMo source runs plus
finalized B ablation artifacts.

- [ ] **Step 1: Verify both source finalizations before any write**

Run the validator in dry/read-only mode if provided. Record all five
`FINALIZED.json` hashes (two base, one controlled, two ablation) and source
mtimes.

- [ ] **Step 2: Generate and validate using Task C8 commands**

Expected: one content-addressed analysis directory and `FINALIZED.json`.

- [ ] **Step 3: Prove source immutability**

Rehash all five source runs and compare mtimes/hashes with Step 1. Any
difference fails Gate D.

- [ ] **Step 4: Inspect claim/review coverage gates**

Confirm controlled activation, binding budgets, both datasets, claim hashes,
and at least 50 failures or all if fewer. Report unreviewed hypotheses as such.

Generated analysis artifacts remain uncommitted unless the Lead/user explicitly
chooses a safe publication format; never commit private traces or caches.

## Workstream C Handoff

Report:

- base `F0`, B schema commit, A policy version, and C commits;
- exact changed files/tests;
- synthetic fixture schema/hash;
- source run IDs/config/finalization hashes consumed;
- derived `analysis_id` and analysis finalization hash;
- factor-isolation/activity/budget-binding validation;
- claim and human-review coverage;
- proof that source hashes/mtimes were unchanged;
- remaining methodological risks;
- confirmation that C executed no source method, live model, GPU, Docker, or
  foreign-path edit.
