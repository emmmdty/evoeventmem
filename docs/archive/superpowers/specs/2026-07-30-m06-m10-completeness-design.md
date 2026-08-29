# M06-M10 Completeness Repair Design

## Purpose

Complete the M06-M10 delivery as one authorized repair pass. The repair must close
the correctness, integration, and redundancy findings discovered during review
without implementing M11 or later production persistence work.

## Goals

- Preserve the durable M06 domain schema and restore backward-compatible `/v1`
  API responses.
- Make deterministic extraction cover event summaries, turns, and observations
  with exact, globally scoped evidence identities.
- Make M08 retries idempotent without collapsing distinct memories supported by
  the same evidence.
- Guarantee all-or-nothing M08 and M10 in-memory writes and serialize concurrent
  read-decide-write operations.
- Make M09 candidate computation bounded before embedding work and use its
  candidates in M10.
- Prevent cross-tenant consolidation, unrelated-event conflicts, and stale
  out-of-order facts from replacing newer current facts.
- Keep feature values, thresholds, rule hits, decision reasons, and evidence
  provenance inspectable.

## Non-Goals

- No database backend, migration framework, async queue, vector database, or
  learned model.
- No M11 query routing or M12 retrieval work.
- No benchmark claim beyond regenerated smoke artifacts.
- No deletion of task modules that are intended to become part of the complete
  write/consolidation path.

## Architecture

### Repository transactions

`MemoryRepository` will retain its existing read/write methods and add a
transaction context. The context yields a repository view with the same
operations. `InMemoryMemoryRepository` will hold its re-entrant lock for the
whole context and write to a snapshot. It publishes the snapshot only when the
context exits successfully; exceptions discard it.

M08 will validate and prepare the full request before opening the transaction.
Duplicate detection and all durable writes then happen inside one transaction.
M10 will read active candidates, generate the bounded candidate set, decide, and
apply every update inside one transaction. This prevents partial writes and
concurrent read-decide-write races in the in-memory implementation.

### Evidence identity and extraction

Evidence identities must be stable within the full imported corpus, not only a
conversation. Turn and observation evidence IDs will include available dataset,
sample, and session scope. Raw local IDs remain in evidence metadata for
inspection.

The rule extractor will use this order:

1. Extract event summaries with the exact summary item as an
   `event_summary` evidence span. A same-session turn may be added as additional
   evidence only when the asserted text is an exact normalized span of that
   turn; token overlap alone is never treated as support.
2. When no event summaries produce candidates, create deterministic candidates
   from turns using the full turn as exact evidence.
3. Create deterministic candidates for observations using stable observation
   indices and the complete observation text as exact evidence.

Point events store `event_time` but do not claim an open-ended validity interval.
The LLM extractor continues to validate exact spans. Cached execution remains a
model-gateway responsibility, but its contract and test will make that
requirement explicit.

### Idempotency and decision logs

The M08 idempotency digest will contain:

- normalized, scoped evidence references;
- extractor version;
- a deterministic candidate identity containing memory kind, normalized
  content, temporal fields, canonical entity/role/relation bindings, and
  explicit fact slot/value metadata.

An exact retry therefore resolves to the existing memory, while two different
facts extracted from one turn remain distinct. Request validation is a first
pass: when any candidate is invalid, every otherwise valid candidate is logged
as rejected before duplicate classification, so no decision points to a memory
that was never committed.

Repository exceptions roll back the transaction. The service returns structured
storage-failure decisions and metrics instead of leaving an incomplete decision
log.

### Bounded linking

Candidate generation will build deterministic lookup structures once per
request:

- normalized entity and alias keys;
- token-to-target indexes;
- explicit fact-slot keys;
- time-window-eligible event targets.

Exact/alias/slot matches are selected directly. Lexical keys form a deterministic
shortlist for embedding candidates. A small deterministic fallback pool is
capped before any embedding calls, so embedding work is bounded by request
limits rather than repository size. Entity and event policies remain distinct.

The result will expose comparison counts in addition to latency and returned
candidate counts. Tests will assert hard upper bounds on embedding calls.

### ETEC integration and temporal semantics

`ETECConsolidator.apply` will use `LinkCandidateGenerator` and consolidate only
the unique target memories returned by its entity/event policies. Explicitly
provided candidates remain supported for deterministic tests but receive the
same user, tenant, status, and identity filtering.

Contradiction rules require an explicit normalized `fact_slot`. Entity/role
fallback may still contribute similarity features but cannot alone establish a
single-valued fact slot. Events with only `event_time` are treated as point
intervals.

For a contradictory single-valued fact:

- "newer" and "older" are determined only by fact-effective time
  (`valid_from`, then `event_time`), never ingestion or creation time;
- a newer incoming fact closes the older target at the incoming effective time,
  marks that target `SUPERSEDED` with `superseded_by` pointing to the incoming
  fact, and stores the incoming fact as `ACTIVE` with a reciprocal `supersedes`
  link;
- an older incoming fact is stored as historical, closed at the newer target's
  effective time, marked `SUPERSEDED` with `superseded_by` pointing to the newer
  target, and linked reciprocally from the target's `supersedes` list;
- an unorderable conflict is rejected with an observable rule hit rather than
  silently replacing current state.

Every updated memory receives decision metadata computed for that memory. The
implementation will not reuse one target's feature vector for other targets.

### API compatibility

The domain model remains canonical and serializes as `memory_kind`,
`evidence_refs`, structured entities, and a list of supersession links.

The existing `/v1` API will use transport response adapters that preserve the
starter response fields and shapes: `kind`, `evidence`, string entity names, and
the legacy scalar `supersedes` value. New domain fields may be exposed through a
future API version, not by silently changing `/v1`.

## Error Handling

- Invalid evidence remains a structured `EvidenceValidationError`.
- Invalid write candidates produce per-candidate validation decisions.
- A mixed invalid request writes nothing and logs every candidate outcome.
- Repository failures roll back and produce `storage_failed` decisions.
- Missing or ambiguous temporal order for a contradiction cannot supersede an
  active fact.
- Candidate-generation fallback is explicit in reasons and metrics.

## Verification

New regression tests will cover:

- legacy `/v1` response field names and shapes;
- turn-only and observation-only rule extraction;
- scoped evidence IDs and rejection of unsupported summary-to-turn links;
- two distinct events from one evidence source both being stored;
- same-content events with different canonical participants receiving distinct
  idempotency identities;
- exact retry deduplication and mixed-invalid decision consistency;
- M08 rollback on an injected repository failure;
- embedding-call bounds independent of repository size;
- M10 use of M09 candidates;
- cross-tenant isolation;
- unrelated extracted events remaining active;
- stale out-of-order facts becoming historical rather than current;
- M10 rollback and concurrent contradiction writes;
- per-target ETEC decision metadata;
- smoke metric behavior using the same fact-slot/value helpers as production.

Every verification command from M06-M10 will run, followed by:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
uv run python -m benchmarks.etec_smoke
```

The smoke command must generate a new result artifact before any metric is
reported.

## Git and Cleanup

Implementation changes will be reviewed as one coherent authorized M06-M10
repair. Generated artifacts, datasets, caches, and local traces remain ignored.
The 5.7 GB LongMemEval download cache will be reported as removable local
redundancy but will not be deleted automatically.
