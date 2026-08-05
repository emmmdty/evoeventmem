# Workstream A: Retrieval and Method Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make router/QEMR temporal behavior, fusion, fallback, evidence
controls, prompt budgeting, and stress-authorized ETEC behavior correct and
observable without changing benchmark or analysis code.

**Architecture:** Keep deterministic routing and fixed-vector baseline
semantics, add explicit temporal constraints, rank time only within relevant
candidates, fuse with weighted reciprocal rank, and pack the exact rendered
reader input through one token estimator. A exposes controls; B executes them.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, uv, Ruff, mypy.

---

## Owned Files

**Create:**

- `src/evoeventmem/tokenization.py`
- `tests/retrieval/test_tokenization.py`
- `tests/retrieval/test_retrieval_contract.py`
- `tests/retrieval/test_query_router_evaluation.py`
- `tests/fixtures/router/m11_query_router_eval.json`
- A-owned regression fixtures below `tests/fixtures/consolidation/` only when
  B's stress runner reproduces a failure

**Modify:**

- `src/evoeventmem/router.py`
- `src/evoeventmem/retrieval.py`
- `src/evoeventmem/consolidation.py` only in Task A8
- `tests/retrieval/test_query_router.py`
- `tests/retrieval/test_qemr.py`
- `tests/consolidation/test_etec.py` only in Task A8
- `benchmarks/retrieval_smoke.py`
- `tests/benchmarks/test_retrieval_smoke.py`
- retrieval/router fixtures owned by A

**Forbidden:** benchmark runners/configs/artifact schemas, `extraction.py`,
analysis, API/infra, project docs, `pyproject.toml`, B's ETEC stress fixture.

**Phase rule:** the contract-only agent executes A1 before `F0`. The persistent
feature worker starts from `F0`, verifies A1's tests, and begins at A2; it does
not reimplement or recommit A1.

### Task A1: Freeze Token and Retrieval Contracts

**Files:**

- Create: `src/evoeventmem/tokenization.py`
- Create: `tests/retrieval/test_tokenization.py`
- Create: `tests/retrieval/test_retrieval_contract.py`
- Modify: `src/evoeventmem/retrieval.py`

- [ ] **Step 1: Write failing estimator identity/message tests**

Add tests equivalent to:

```python
def test_estimator_counts_complete_messages() -> None:
    estimator = DeterministicTokenEstimator(name="test", version="v1")
    messages = [
        ChatMessage(role="system", content="Use cited evidence."),
        ChatMessage(role="user", content="Question: 你好？"),
    ]
    estimate = estimator.count_messages(messages)
    assert estimate.estimator_name == "test"
    assert estimate.estimator_version == "v1"
    assert estimate.message_overhead_tokens > 0
    assert estimate.total_tokens >= estimate.content_tokens
```

Also cover Unicode, punctuation, empty messages, and deterministic repeatability.

- [ ] **Step 2: Write failing retrieval schema tests**

Require request/result fields for temporal constraint, component raw score/rank,
fusion contribution, source failure events, exclusions, evidence policy,
rendered message budget, and content/overhead/total counts. Assert every packed
item rejects empty provenance under both policies.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q \
  tests/retrieval/test_tokenization.py \
  tests/retrieval/test_retrieval_contract.py -x
```

Expected: import/model-field failures because these contracts do not exist.

- [ ] **Step 4: Implement only the contract surface**

Use focused Pydantic/dataclass models. The public evidence policy is:

```python
class EvidencePolicy(StrEnum):
    CONSTRAINED = "constrained"
    PROVENANCE_ONLY = "provenance_only"
```

Both modes require `PackedItem.evidence_refs`. Add no retrieval behavior beyond
what the contract tests require.

- [ ] **Step 5: Verify and commit the contract-only change**

```bash
uv run pytest -q \
  tests/retrieval/test_tokenization.py \
  tests/retrieval/test_retrieval_contract.py
uv run mypy src
git add src/evoeventmem/tokenization.py src/evoeventmem/retrieval.py \
  tests/retrieval/test_tokenization.py \
  tests/retrieval/test_retrieval_contract.py
git commit -m "feat(retrieval): freeze token and retrieval contracts"
```

Handoff this commit to the Integration Lead for `F0`. Do not begin feature
work on a branch that does not contain the merged registry freeze.

### Task A2: Add Explicit Temporal Query Semantics

**Files:**

- Modify: `src/evoeventmem/router.py`
- Modify: `tests/retrieval/test_query_router.py`

- [ ] **Step 1: Add failing operator tests**

Cover `none`, `at`, `before`, `after`, `between`, `earliest`, `latest`,
`sequence`, and `duration`. Key regression:

```python
def test_unconstrained_when_has_no_latest_constraint() -> None:
    decision = QueryRouter().route("When did Caroline move?")
    assert decision.intent is QueryIntent.TEMPORAL
    assert decision.temporal_constraint.operator is TemporalOperator.NONE
```

Date bounds must be UTC-aware and decisions must expose matched spans, rule
hits, and reason. Relative dates require an explicit UTC `reference_time`.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest -q tests/retrieval/test_query_router.py \
  -k 'temporal_constraint or operator or utc or unconstrained_when' -x
```

Expected: missing temporal constraint/operator fields.

- [ ] **Step 3: Implement deterministic parsing**

Add small pure parsing helpers in `router.py`. Preserve `QueryIntent`: a query
may have temporal answer intent while its operator remains `none`.

- [ ] **Step 4: Verify full router behavior**

```bash
uv run pytest -q tests/retrieval/test_query_router.py
```

Expected: existing labels/fallback tests and new operator tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/evoeventmem/router.py tests/retrieval/test_query_router.py
git commit -m "feat(router): add explicit temporal query constraints"
```

### Task A3: Build an Independent Router Evaluation

**Files:**

- Create: `tests/fixtures/router/m11_query_router_eval.json`
- Create: `tests/retrieval/test_query_router_evaluation.py`
- Modify: `src/evoeventmem/router.py` only for reproducible failures

- [ ] **Step 1: Create a benchmark-style evaluation fixture**

Each case has `case_id`, query, expected intent, expected temporal operator,
ambiguity/adversarial flags, and provenance note. Include positives and
negatives for every label/operator. Do not copy the 21-case development fixture.

- [ ] **Step 2: Add failing evaluation tests**

Require fixture hash inequality, confusion matrix, per-label F1, Macro-F1,
temporal-operator accuracy, fallback rate, and fixed confidence bins. The test
must fail if development and evaluation case/query sets are identical.

- [ ] **Step 3: Verify RED/evaluate current rules**

```bash
uv run pytest -q tests/retrieval/test_query_router_evaluation.py -x
```

Expected: missing evaluation helper/report and possibly reproducible rule
errors. Record current metrics before changing rules.

- [ ] **Step 4: Fix only reproducible routing defects**

Update deterministic cues/rules without adding benchmark-specific schemas or
training. Do not tune to hide fallback cases; report them.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/retrieval/test_query_router.py \
  tests/retrieval/test_query_router_evaluation.py
git add src/evoeventmem/router.py \
  tests/fixtures/router/m11_query_router_eval.json \
  tests/retrieval/test_query_router_evaluation.py
git commit -m "test(router): add independent benchmark evaluation"
```

### Task A4: Make Temporal Retrieval Relevance-First

**Files:**

- Modify: `src/evoeventmem/retrieval.py`
- Modify: `tests/retrieval/test_qemr.py`

- [ ] **Step 1: Add temporal ranking regressions**

Write controlled tests showing:

- an unrelated newest memory cannot beat an older query-relevant memory for an
  unconstrained `when` query;
- `latest`/`earliest` order applies only inside a relevant pool;
- `before`/`after`/`between` use interval agreement;
- explicit sequence/duration constraints remain observable;
- fixed vector results remain unchanged.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest -q tests/retrieval/test_qemr.py \
  -k 'unrelated_recent or relevant_pool or before or after or between or fixed_vector' -x
```

Expected: current unconditional `_temporal_candidates()` recency promotes the
wrong item.

- [ ] **Step 3: Implement two-stage candidates**

Dense/entity/relation evidence creates the relevance pool. Temporal constraints
filter or rerank only this pool. For operator `none`, time presence is a small
feature and cannot dominate semantic relevance.

- [ ] **Step 4: Verify temporal and legacy behavior**

```bash
uv run pytest -q tests/retrieval/test_qemr.py
```

Expected: all existing budget/provenance/supersession behavior remains green.

- [ ] **Step 5: Commit**

```bash
git add src/evoeventmem/retrieval.py tests/retrieval/test_qemr.py
git commit -m "fix(retrieval): condition temporal ranking on relevance"
```

### Task A5: Replace Per-Source Max Fusion with Observable WRRF

**Files:**

- Modify: `src/evoeventmem/retrieval.py`
- Modify: `tests/retrieval/test_qemr.py`

- [ ] **Step 1: Add failing fusion tests**

Require an irrelevant single-candidate source not to receive artificial `1.0`
authority. Assert deterministic weighted reciprocal-rank contributions and
stable tie-breaking. Persist raw score, per-source rank, weight, and fusion
contribution for every component.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest -q tests/retrieval/test_qemr.py \
  -k 'wrrf or source_winner or fusion_contribution or tie' -x
```

Expected: current `_normalize()` max scaling or missing component fields fail.

- [ ] **Step 3: Implement WRRF only for hybrid/QEMR paths**

Use a fixed declared `rrf_k` and predeclared weights. Do not train/tune them on
reported LoCoMo outcomes. Keep the fixed-vector dense ordering unchanged.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest -q tests/retrieval/test_qemr.py
git add src/evoeventmem/retrieval.py tests/retrieval/test_qemr.py
git commit -m "fix(retrieval): use observable weighted rank fusion"
```

### Task A6: Add Observable Failures and Public Method Controls

**Files:**

- Modify: `src/evoeventmem/retrieval.py`
- Modify: `tests/retrieval/test_qemr.py`

- [ ] **Step 1: Add failing source-failure tests**

Use a source stub that raises. Require result events with source, stable reason
code, degraded policy, and duration. Assert QEMR never silently reports itself
as fixed vector.

- [ ] **Step 2: Add controlled-switch tests**

Test disabling temporal, disabling graph, rule versus forced routing, fixed
vector/fixed hybrid/QEMR strategy, weight profile, evidence policy, and budget.
Each synthetic pair must change at least one selected/excluded/ranked/packed
decision while every other input stays equal.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/retrieval/test_qemr.py \
  -k 'failure or degraded or disabled or forced or evidence_policy' -x
```

Expected: exceptions currently escape and controls are not one public model.

- [ ] **Step 4: Implement one `RetrievalControls` path**

B must be able to pass controls but not duplicate their semantics. Under
`provenance_only`, disable evidence eligibility/coverage/scoring effects only;
never allow missing evidence into a durable or packed item.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/retrieval/test_qemr.py \
  tests/retrieval/test_retrieval_contract.py
git add src/evoeventmem/retrieval.py tests/retrieval/test_qemr.py \
  tests/retrieval/test_retrieval_contract.py
git commit -m "feat(retrieval): expose observable retrieval controls"
```

### Task A7: Enforce the Complete Reader-Input Budget

**Files:**

- Modify: `src/evoeventmem/tokenization.py`
- Modify: `src/evoeventmem/retrieval.py`
- Modify: `tests/retrieval/test_tokenization.py`
- Modify: `tests/retrieval/test_qemr.py`

- [ ] **Step 1: Add failing full-prompt budget tests**

Require accounting for evidence labels, metadata, separators, question,
reader directive, system/user roles, and chat overhead. Set a high item cap and
prove the token budget binds first.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest -q tests/retrieval/test_tokenization.py \
  tests/retrieval/test_qemr.py -k 'budget or estimator or overhead' -x
```

Expected: current `split()` count omits prompt fields/overhead.

- [ ] **Step 3: Render and estimate from one source**

Packing must reserve fixed overhead before item selection. Return the rendered
reader messages, or an immutable rendering input used by B without alternate
string assembly. Persist `content_tokens`, `prompt_overhead_tokens`, and
`total_input_tokens_estimate`.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest -q tests/retrieval/test_tokenization.py \
  tests/retrieval/test_qemr.py
uv run mypy src
git add src/evoeventmem/tokenization.py src/evoeventmem/retrieval.py \
  tests/retrieval/test_tokenization.py tests/retrieval/test_qemr.py
git commit -m "fix(retrieval): enforce complete reader input budgets"
```

### Task A8: Validate Retrieval Smoke and Repair Only Reproduced ETEC Failures

**Files:**

- Modify: `benchmarks/retrieval_smoke.py`
- Modify: `tests/benchmarks/test_retrieval_smoke.py`
- Conditional modify: `src/evoeventmem/consolidation.py`
- Conditional modify: `tests/consolidation/test_etec.py`
- Conditional create: `tests/fixtures/consolidation/<case-id>.json`

- [ ] **Step 1: Extend smoke regressions**

Cover unrelated recency, WRRF components, fallback events, controlled switch
deltas, mandatory provenance, and complete budget breakdown.

- [ ] **Step 2: Verify retrieval smoke**

```bash
uv run pytest -q tests/benchmarks/test_retrieval_smoke.py
uv run python -m benchmarks.retrieval_smoke
```

Expected: deterministic success and an observable report for every gate.

- [ ] **Step 3: Wait for B's exact stress commit**

Record the fixture hash and run:

```bash
uv run pytest -q tests/benchmarks/test_etec_stress.py
uv run python -m benchmarks.experiments.etec_stress \
  --fixture benchmarks/experiments/fixtures/etec_stress_v1.json \
  --output-root artifacts/smoke/etec-stress
```

Expected: every declared case ID is present. MERGE and SUPERSEDE action counts
must both be nonzero.

- [ ] **Step 4: For each failure, add an A-owned RED regression**

Copy only the minimal failing input into
`tests/fixtures/consolidation/<case-id>.json`. Add a named unit test and run it
alone to confirm the same behavior fails.

- [ ] **Step 5: Implement the smallest consolidation correction**

Preserve evidence lineage, UTC intervals, and scope isolation. Do not tune a
threshold because a dataset metric did not improve.

- [ ] **Step 6: Verify Gate A**

```bash
uv run pytest -q tests/retrieval
uv run pytest -q tests/consolidation/test_etec.py
uv run pytest -q tests/benchmarks/test_retrieval_smoke.py \
  tests/benchmarks/test_etec_stress.py
uv run python -m benchmarks.retrieval_smoke
uv run ruff check src/evoeventmem/router.py src/evoeventmem/retrieval.py \
  src/evoeventmem/consolidation.py src/evoeventmem/tokenization.py \
  tests/retrieval tests/consolidation
uv run mypy src
```

Expected: all pass; stress output contains every case and action-stratified
counts. If stress already passes, do not manufacture a consolidation diff.

- [ ] **Step 7: Commit smoke/conditional regressions**

```bash
git add benchmarks/retrieval_smoke.py \
  tests/benchmarks/test_retrieval_smoke.py \
  src/evoeventmem/consolidation.py tests/consolidation/test_etec.py \
  tests/fixtures/consolidation
git commit -m "test(retrieval): cover remediation method gates"
```

If no consolidation file changed, stage only smoke files.

## Workstream A Handoff

Report:

- base `F0` and A commit SHAs;
- exact changed paths;
- router, retrieval, consolidation, and estimator versions;
- controls schema and controlled decision deltas;
- source failure/fallback reason-code table;
- stress fixture hash and action counts;
- exact command outputs;
- unresolved risks/interface requests;
- confirmation that no live model, GPU, Docker, source run, or foreign path was
  touched.
