# M06-M10 Completeness Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task-by-task.
> Every behavior change uses `superpowers:test-driven-development`.

**Goal:** Close all reviewed M06-M10 correctness and integration gaps while
preserving task boundaries and the canonical domain schema.

**Architecture:** Add a transaction context to the repository port, keep legacy
API representation in transport adapters, use globally scoped exact evidence,
make write idempotency candidate-aware, bound M09 work before embeddings, and
make M10 consume M09 candidates inside one read-decide-write transaction.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, uv, Ruff, mypy.

---

## File Ownership and Order

1. Repository transaction foundation:
   `core/ports.py`, `infra/in_memory_repository.py`, transaction tests.
2. M06 compatibility:
   `api/app.py`, `tests/test_api.py`.
3. M07 extraction:
   `extraction.py`, `tests/extraction/test_event_extraction.py`.
4. M08 write pipeline:
   `services/memory_service.py`, `tests/services/test_write_pipeline.py`.
5. M09 bounded linking:
   `linking.py`, `tests/linking/test_candidate_generation.py`.
6. M10 ETEC:
   `consolidation.py`, `tests/consolidation/test_etec.py`.
7. Benchmark alignment and final integration:
   `benchmarks/etec_smoke.py`, fixture/tests as needed.

Later tasks may depend on earlier files but must not rewrite their established
contracts. Implementers must not revert edits from other workers.

### Task 1: Atomic Repository Transactions

**Files:**
- Modify: `src/evoeventmem/core/ports.py`
- Modify: `src/evoeventmem/infra/in_memory_repository.py`
- Create: `tests/infra/test_in_memory_repository.py`

- [ ] **Step 1: Add failing rollback and isolation tests**

Create tests that:

```python
def test_transaction_rolls_back_all_writes_on_error() -> None:
    repository = InMemoryMemoryRepository()
    with pytest.raises(RuntimeError):
        with repository.transaction() as transaction:
            transaction.add(first)
            transaction.add(second)
            raise RuntimeError("fail")
    assert repository.list_for_user("u1") == []


def test_transaction_publishes_all_writes_on_success() -> None:
    repository = InMemoryMemoryRepository()
    with repository.transaction() as transaction:
        transaction.add(first)
        transaction.add(second)
    assert {item.memory_id for item in repository.list_for_user("u1")} == {
        first.memory_id,
        second.memory_id,
    }
```

Also use two threads and events to prove one transaction cannot observe or
interleave another transaction's partially updated state. The first transaction
writes and signals while holding the lock. The second attempts to enter and must
remain blocked. Release the first transaction, use bounded joins/timeouts, then
assert the second sees only the fully published snapshot. Do not wait on a
two-party barrier while the first thread owns the transaction lock.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest -q tests/infra/test_in_memory_repository.py
```

Expected: failure because `transaction()` does not exist.

- [ ] **Step 3: Implement the transaction port and snapshot repository**

Add to `MemoryRepository`:

```python
def transaction(self) -> ContextManager[MemoryRepository]: ...
```

Implement `InMemoryMemoryRepository.transaction()` as a context manager that:

- holds the existing `RLock` for the complete context;
- copies `_items` before yielding;
- applies `add/get/list_for_user` to the working copy;
- publishes only after successful exit;
- discards the working copy on any exception.

Do not expose storage implementation details to service/domain layers.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
uv run pytest -q tests/infra/test_in_memory_repository.py
uv run mypy src
```

- [ ] **Step 5: Commit**

```bash
git add src/evoeventmem/core/ports.py src/evoeventmem/infra/in_memory_repository.py tests/infra/test_in_memory_repository.py
git commit -m "feat: add atomic memory repository transactions"
```

### Task 2: M06 Legacy API Compatibility

**Files:**
- Modify: `src/evoeventmem/api/app.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Add failing legacy response tests**

Extend API tests to assert both create and search responses preserve:

```python
assert memory["kind"] == "event"
assert memory["entities"] == ["coding agent", "dependency"]
assert memory["evidence"] == [
    {"source_type": "test", "source_id": "api-1", "locator": None, "quote": None}
]
assert "memory_kind" not in memory
assert "evidence_refs" not in memory
```

Test a legacy scalar `supersedes` response as well.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest -q tests/test_api.py
```

- [ ] **Step 3: Add transport-only response adapters**

Define private Pydantic response models in `api/app.py` for the starter `/v1`
shape. Add `from_domain()` constructors that convert structured entities to
names, use `kind/evidence`, and expose the first supersession link as the legacy
scalar. Add a search-hit adapter. Keep `MemoryRecord.to_json_dict()` canonical.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
uv run pytest -q tests/test_api.py tests/domain/test_event_schema.py
```

- [ ] **Step 5: Commit**

```bash
git add src/evoeventmem/api/app.py tests/test_api.py
git commit -m "fix: preserve legacy v1 memory responses"
```

### Task 3: M07 Exact Scoped Extraction

**Files:**
- Modify: `src/evoeventmem/extraction.py`
- Modify: `tests/extraction/test_event_extraction.py`

- [ ] **Step 1: Add failing extraction regressions**

Add tests for:

- event summaries citing exact `event_summary` evidence IDs scoped by
  dataset/sample/session;
- a summary paraphrase not being labeled as a supporting turn span;
- turn-only input producing one exact turn-backed candidate;
- observation-only input producing one exact observation-backed candidate;
- identical local turn IDs in different samples producing different evidence
  IDs;
- event memories having `event_time` but no open-ended `valid_from`;
- direct `LLMEventExtractor` use documenting that cached execution requires a
  `CachedChatModel`.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest -q tests/extraction
```

- [ ] **Step 3: Implement scoped evidence helpers and rule fallbacks**

Add small pure helpers:

```python
def _evidence_scope(request: ExtractionInput, *parts: str) -> str: ...
def _summary_evidence(...) -> EvidenceRef: ...
def _turn_evidence(...) -> EvidenceRef: ...
def _observation_evidence(...) -> EvidenceRef: ...
```

Evidence metadata retains raw IDs, session ID, dataset, and sample ID. Summary
events use exact summary text as evidence. Turns are additional evidence only
for exact normalized spans. When no summary candidates exist, extract turns;
always add distinct observation candidates. Deduplicate exact candidates
deterministically.

Change `_build_memory()` so point events set `event_time` and leave
`valid_from/valid_to` unset.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
uv run pytest -q tests/extraction
uv run pytest -q tests/domain/test_event_schema.py
```

- [ ] **Step 5: Commit**

```bash
git add src/evoeventmem/extraction.py tests/extraction/test_event_extraction.py
git commit -m "fix: extract exact scoped event evidence"
```

### Task 4: M08 Atomic Candidate-Aware Writes

**Files:**
- Modify: `src/evoeventmem/services/memory_service.py`
- Modify: `tests/services/test_write_pipeline.py`

- [ ] **Step 1: Add failing M08 regressions**

Add tests proving:

- two different contents from the same evidence are both accepted;
- same evidence/content/time with different canonical entities or roles are
  both accepted;
- an exact retry remains duplicate-safe;
- a mixed-invalid request has no `DUPLICATE` decision pointing to an unwritten
  memory;
- a repository exception on the second add rolls back the first add and returns
  `storage_failed` decisions/metrics.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest -q tests/services/test_write_pipeline.py
```

- [ ] **Step 3: Implement two-pass validation and candidate identity**

Canonicalize candidate identity using:

- memory kind and normalized content;
- event/valid interval;
- sorted entity IDs/names/kinds/roles;
- sorted role mappings and relations;
- explicit fact slot/value and multi-valued metadata.

Include that identity with scoped evidence and extractor version in the digest.
Validate all candidates first. Abort the complete request before duplicate
classification when any validation fails.

Open one repository transaction for duplicate lookup and durable writes. Catch
storage exceptions after rollback and create structured storage-failure
decisions for every candidate that was not already validation-rejected.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
uv run pytest -q tests/services/test_write_pipeline.py
uv run pytest -q tests/test_memory_service.py
```

- [ ] **Step 5: Commit**

```bash
git add src/evoeventmem/services/memory_service.py tests/services/test_write_pipeline.py
git commit -m "fix: make memory writes atomic and candidate-aware"
```

### Task 5: M09 Bounded Candidate Work

**Files:**
- Modify: `src/evoeventmem/linking.py`
- Modify: `tests/linking/test_candidate_generation.py`
- Modify: `tests/fixtures/linking/m09_tiny_linking.json` only if a new explicit
  fact-slot case is required.

- [ ] **Step 1: Add failing bound and policy tests**

Use a counting embedding model to prove that increasing `existing` from 100 to
10,000 does not increase embedding calls past a limit derived from request
candidate limits. Add tests for:

- normalized and alias inverted-key matches;
- explicit fact-slot matches;
- event time-window filtering;
- deterministic fallback reasons and comparison counts;
- status/user/tenant filtering;
- distinct entity and event policies;
- recall@K on the inspectable fixture.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest -q tests/linking
```

- [ ] **Step 3: Implement deterministic indexes and capped pools**

Build per-request indexes in one scan. Resolve exact keys directly, gather
lexical/token candidates through inverted indexes, and add a stable fallback
ordered by target memory/entity identity. Cap the target pool before embedding.

Extend result/metrics with entity/event comparison counts. Compute each unique
embedding at most once per request. Do not add an external index dependency.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
uv run pytest -q tests/linking
uv run mypy src
```

- [ ] **Step 5: Commit**

```bash
git add src/evoeventmem/linking.py tests/linking/test_candidate_generation.py tests/fixtures/linking/m09_tiny_linking.json
git commit -m "fix: bound linking work before embeddings"
```

### Task 6: M10 Transactional ETEC Integration

**Files:**
- Modify: `src/evoeventmem/consolidation.py`
- Modify: `tests/consolidation/test_etec.py`

- [ ] **Step 1: Add failing ETEC regressions**

Add tests for:

- M10 calling M09 and only scoring returned target memories;
- the existing positional constructor
  `ETECConsolidator(embedding_model, thresholds)` remaining compatible;
- explicit candidate lists still filtering status, user, and tenant;
- ADD of a point event preserving `event_time` while keeping both
  `valid_from` and `valid_to` unset;
- two unrelated rule-extracted events for one speaker remaining active;
- cross-tenant facts never merging or superseding;
- a newer fact closing and superseding an older target with reciprocal links;
- a stale fact being stored closed and superseded by the newer target;
- both a missing effective time and an equal-effective-time contradiction being
  rejected observably without status/link mutation;
- repository failure rolling back every changed memory;
- two concurrent contradictory writes leaving one current active fact;
- each superseded target storing its own target ID/features in ETEC metadata;
- one embedding batch call per scored pair.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest -q tests/consolidation
```

- [ ] **Step 3: Integrate linking and repair temporal rules**

Construct or inject a `LinkCandidateGenerator` from the same embedding model
with a backward-compatible constructor:

```python
def __init__(
    self,
    embedding_model: EmbeddingModel,
    thresholds: ETECThresholds | None = None,
    *,
    candidate_generator: LinkCandidateGenerator | None = None,
) -> None: ...
```

Within `repository.transaction()`:

1. filter existing records by user, tenant, active status, and identity;
2. call M09 and deduplicate its entity/event target memories;
3. decide only over the bounded targets;
4. apply ADD/MERGE/SUPERSEDE/REJECT atomically.

Contradictions require an explicit `fact_slot`. Treat event-only time as the
closed comparison interval `[event_time, event_time]`, but never persist that
point as an open-ended validity interval. ADD must preserve `event_time` and
leave both validity fields unset when the source is a point event. Compare
fact-effective times and implement both newer and stale reciprocal-link paths.
Equal or missing effective times reject. Build a separate decision for every
changed target.

Cache the pair embedding response and index both vectors from that one response.
Promote public `fact_slot_key()` and `fact_value_key()` helpers here so the
benchmark can consume the exact production semantics in Task 7.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
uv run pytest -q tests/consolidation
uv run pytest -q tests/linking
```

- [ ] **Step 5: Commit**

```bash
git add src/evoeventmem/consolidation.py tests/consolidation/test_etec.py
git commit -m "fix: integrate transactional temporal consolidation"
```

### Task 7: Smoke Alignment and End-to-End Verification

**Files:**
- Modify: `benchmarks/etec_smoke.py`
- Modify: `tests/fixtures/consolidation/m10_etec_annotations.json`
- Create: `tests/benchmarks/test_etec_smoke.py`

- [ ] **Step 1: Add failing smoke metric regressions**

Use the public fact-slot/value normalization established in Task 6. Add fixture
cases for:

- unrelated events with no fact slot;
- stale out-of-order facts;
- multi-valued slots;
- tenant isolation where appropriate.

Assert `expected_action` for every case and keep merge F1, conflict accuracy,
provenance coverage, and stale-memory error deterministic.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest -q tests/consolidation tests/benchmarks
```

- [ ] **Step 3: Align benchmark logic and fixtures**

Remove the duplicate benchmark `_fact_slot/_fact_value` implementation. Import
the production helpers. Keep smoke outputs write-once and include decision
features, thresholds, rule hits, and reasons.

- [ ] **Step 4: Verify benchmark edits**

First run the tests that cover the pending benchmark edits:

```bash
uv run pytest -q tests/benchmarks/test_etec_smoke.py tests/consolidation
```

- [ ] **Step 5: Commit benchmark code before artifact generation**

```bash
git add benchmarks/etec_smoke.py tests/benchmarks/test_etec_smoke.py tests/fixtures/consolidation/m10_etec_annotations.json
git commit -m "test: strengthen ETEC smoke coverage"
```

- [ ] **Step 6: Run all task acceptance commands**

```bash
uv run pytest -q tests/domain/test_event_schema.py
uv run pytest -q tests/extraction
uv run pytest -q tests/services/test_write_pipeline.py
uv run pytest -q tests/linking
uv run pytest -q tests/consolidation
uv run python -m benchmarks.etec_smoke
```

- [ ] **Step 7: Run repository-wide verification**

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
git diff --check
git status --short
```

- [ ] **Step 8: Review generated artifact**

Confirm the new M10 smoke artifact contains all required metrics and points to
the Task 7 Git commit. Do not commit the artifact.

### Task 8: Final Review and Cleanup

**Files:** All files changed by Tasks 1-7.

- [ ] **Step 1: Run an independent spec-compliance review**

Check every goal and regression in:

`docs/superpowers/specs/2026-07-30-m06-m10-completeness-design.md`

- [ ] **Step 2: Run an independent code-quality review**

Prioritize correctness, transaction semantics, temporal invariants, provenance,
bounded work, type safety, and missing tests. Fix all blocking/high findings and
re-run affected verification.

- [ ] **Step 3: Commit any review fixes**

Use a focused commit message and include only reviewed tracked changes.

- [ ] **Step 4: Regenerate the final smoke artifact**

Run after the final tracked commit so `git_commit` exactly identifies the code
under evaluation:

```bash
uv run python -m benchmarks.etec_smoke
```

Inspect the new summary and do not commit the ignored artifact.

- [ ] **Step 5: Inspect the final Git history and worktree**

```bash
git log --oneline -10
git status --short
```

Expected: coherent task commits and a clean tracked worktree.
