# M11-M16 Remediation Contract Registry

This registry freezes the shared interfaces that more than one workstream
consumes. It is owned by the Integration Lead and is the single source of truth
for contract versions, owners, invariants, serialization, and compatibility
policy. Feature workstreams A, B, C, and D diverge only after these contracts
are frozen at commit `F0`. Any later contract change is a stop condition.

## Freeze Baseline

- `B0` (reviewed WIP baseline): `25f7783` `chore: preserve reviewed M15-M16 WIP baseline`
- Integration branch: `remediation/m11-m16-integration`
- Lead config: `880b91f` `chore: ignore dataset symlink in worktrees`
- Candidate registry commit: `f0ca238` `docs: draft M11-M16 contract registry`
- `F0` (freeze): (this commit)

## Contract Index

| ID | Owner | Surface | Producer | Consumers |
|----|-------|---------|----------|-----------|
| `A-TOKEN` | A | `TokenEstimator` in `src/evoeventmem/tokenization.py` | A | A, B (budget packing) |
| `A-RETRIEVAL` | A | retrieval request/result/control schema in `src/evoeventmem/retrieval.py` | A | A, B (ablation execution) |
| `B-ARTIFACT` | B | `RunManifest`, `AblationRunManifest`, finalization records in `benchmarks/common/artifacts.py` | B | B, C (analysis) |
| `D-SCOPE` | D | `RequestScope` + additive async production ports in `src/evoeventmem/core/ports.py` | D | D, API/tests |

## Contract Details

### A-TOKEN: TokenEstimator

- **Owner:** Workstream A.
- **Implementation path:** `src/evoeventmem/tokenization.py` (new).
- **Surface:** a `TokenEstimator` object exposing:
  - a declared `name` and `version`;
  - `count_messages(messages: Sequence[ChatMessage]) -> TokenEstimate`;
  - `TokenEstimate` with `content_tokens`, `message_overhead_tokens`,
    `total_tokens`, `estimator_name`, `estimator_version`.
- **Producers/consumers:** A produces; B consumes through retrieval packing.
- **Invariants:** counts complete reader messages (system/user/assistant);
  `total_tokens >= content_tokens`; Unicode and punctuation aware; deterministic
  repeatability for a fixed input.
- **Canonical serialization/hash:** JSON of `(name, version, content_tokens,
  message_overhead_tokens, total_tokens)`.
- **Compatibility policy:** additive only; a version change is a stop condition.
- **Test:** `tests/retrieval/test_tokenization.py`.
- **Consumer impact:** B's budget packing records content/overhead/total counts
  from this estimator rather than `content.split()`.

### A-RETRIEVAL: Retrieval Requests, Results, and Controls

- **Owner:** Workstream A.
- **Implementation path:** `src/evoeventmem/retrieval.py`.
- **Surface:** retrieval request/result models and one public `RetrievalControls`
  path carrying:
  - intent and explicit temporal constraint (operator, bounds, UTC);
  - per-component raw score/rank, weight, and fusion contribution;
  - source failure events (source, stable reason code, degraded policy, duration);
  - exclusions and fallback state (no silent QEMR-to-vector substitution);
  - evidence policy (`constrained` | `provenance_only`);
  - rendered reader-input budget with `content_tokens`,
    `prompt_overhead_tokens`, `total_input_tokens_estimate`;
  - packed items that retain non-empty source provenance under both policies.
- **Producers/consumers:** A produces; B consumes via ablation controls.
- **Invariants:** deterministic weighted reciprocal-rank fusion; fixed-vector
  baseline unchanged; temporal ranking conditional on relevance; every durable
  and packed item retains non-empty `evidence_refs`.
- **Test:** `tests/retrieval/test_retrieval_contract.py`.
- **Consumer impact:** B instantiates A's public controls, never private
  weights or duplicated routing/retrieval.

### B-ARTIFACT: Run Manifests and Finalization

- **Owner:** Workstream B.
- **Implementation path:** `benchmarks/common/artifacts.py`.
- **Surface:** typed `RunManifest`, `AblationRunManifest`, `ExtractionSnapshot`,
  `ExtractionRejection`, retrieval/evidence/consolidation records, and
  write-once `FinalizationRecord`.
- **Manifest fields:** dataset path/hash and scope; complete method set;
  independent reader, extractor, embedding provider/model/version and endpoint;
  tokenizer/estimator name/version; extraction, router, retrieval,
  consolidation policy versions; input budget and candidate/item caps; Git
  commit and dirty status; config hash; expected sample/question IDs.
- **Finalization:** write-once `FINALIZED.json` containing manifest hash and a
  hash for every required artifact; refuses hash drift or overwrite; resume
  refuses manifest drift; `smoke`/`diagnostic`/`publication` are distinct
  classes; only clean, complete publication runs finalize as publication
  evidence.
- **Producers/consumers:** B produces; C consumes via loaders.
- **Canonical serialization/hash:** canonical JSON of the manifest; hashes
  recorded in `FINALIZED.json`.
- **Test:** `tests/benchmarks/test_artifact_contract.py`,
  `tests/benchmarks/test_artifacts.py`.
- **Consumer impact:** C imports B's models and hashes; it never redefines
  producer schemas.

### D-SCOPE: RequestScope and Async Production Ports

- **Owner:** Workstream D.
- **Implementation path:** `src/evoeventmem/core/ports.py` (additive).
- **Surface:** `RequestScope` (nonempty tenant and user, optional session
  narrowing); additive async production repository and embedding ports:
  scoped `add`/`get`/`update`/`list`/vector search/ping-schema/close; an async
  embedding port exposing declared `model_id`, `dimension`, and query/document
  embedding; a typed vector carrying `model_id`, `dimension`, and finite
  numeric values; document vector passed separately from the durable
  `MemoryRecord`; `search_vector` receives query vector plus scope/limit;
  all UUID lookups require `RequestScope`.
- **Preservation:** the synchronous `MemoryRepository` and domain `MemoryRecord`
  remain unchanged.
- **Producers/consumers:** D produces; production API/service consumes.
- **Invariants:** missing tenant/user invalid; scope/body mismatch explicit;
  async fakes enforce the same isolation/dimension rules expected from
  PostgreSQL.
- **Test:** `tests/infra/test_async_repository_contract.py`,
  `tests/infra/test_async_embedding.py`, `tests/api/test_request_scope.py`.
- **Consumer impact:** D's API and async service use the frozen async ports;
  no event-loop thread or shared single connection.

## Compatibility Policy

- Frozen contracts may change only through an approved interface request from
  the owning workstream, after Lead approval, following the interface-request
  template in the master plan.
- Consumer workstreams import the producer's models; they never reimplement or
  redefine a producer schema.
- LongMemEval and LoCoMo may use different resolved model stacks; compatibility
  is enforced within each dataset's method comparison and within each paired
  ablation family, not across datasets.

## Implementation Commits

All owner commits were merged into the integration branch before `F0`.

- A-TOKEN / A-RETRIEVAL freeze: `1e726b0` `feat(retrieval): freeze token and retrieval contracts`
- B-ARTIFACT freeze: `86dea3b` `feat(benchmarks): freeze immutable artifact contracts`
- D-SCOPE freeze: `0201b8f` `feat(core): freeze scoped async production ports`
- C consumer contract: `286003b` `test(analysis): freeze benchmark artifact consumers`
- Lead pytest import-mode fix: `pyproject.toml` `--import-mode=importlib` (resolves the `test_artifact_contract.py` basename collision between B and C)