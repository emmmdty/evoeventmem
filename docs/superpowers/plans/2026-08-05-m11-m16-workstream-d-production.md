# Workstream D: Production Service Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current thread-backed PostgreSQL WIP with a scoped async
pool/pgvector production service whose persistence, failures, API schema, and
observability are verified against a real database.

**Architecture:** Preserve the synchronous research repository, add a separate
scope-aware async production port, extract shared business rules into pure
functions, and run FastAPI through an async application service. PostgreSQL uses
an asyncpg pool and scoped pgvector SQL; production fails closed unless an
explicitly declared development fallback exposes degradation everywhere.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, asyncpg, PostgreSQL
16/pgvector, pytest, uv, Docker Compose, OpenAPI, Ruff, mypy.

---

## Owned Files

**Preserve and modify after WIP baseline:**

- `Dockerfile`
- `docker-compose.yml`
- `api/openapi.json`
- `scripts/compose_smoke.py`
- `scripts/generate_openapi.py`
- `src/evoeventmem/api/`
- `src/evoeventmem/core/ports.py` additive production contracts only
- `src/evoeventmem/infra/`
- `src/evoeventmem/services/memory_service.py`
- `tests/api/`
- `tests/infra/`
- `tests/services/` when shared production rules require tests

**Create:**

- `src/evoeventmem/services/memory_rules.py`
- `src/evoeventmem/services/async_memory_service.py`
- `src/evoeventmem/infra/async_in_memory_repository.py`
- `src/evoeventmem/infra/async_embedding.py`
- `src/evoeventmem/infra/sql/0001_core.sql` and
  `src/evoeventmem/infra/sql/0002_pgvector.sql` if SQL files improve review
- `tests/services/test_memory_rules.py`
- `tests/services/test_async_memory_service.py`
- `tests/infra/test_async_repository_contract.py`
- `tests/infra/test_async_embedding.py`
- `tests/infra/test_pgvector_search.py`
- `tests/infra/conftest.py` when needed for a fail-not-skip PG gate
- `tests/api/test_request_scope.py`

**Forbidden:** research retrieval/router/consolidation, benchmark/analysis
paths, `TASKS.md`/index/docs, and `pyproject.toml` (dependency requests go to
the Lead).

**Phase rule:** the contract-only agent executes D1 before `F0`. The persistent
feature worker starts from `F0`, verifies D1's tests, and begins at D2; it does
not reimplement or recommit D1.

### Task D1: Freeze `RequestScope` and Async Production Ports

**Files:**

- Modify: `src/evoeventmem/core/ports.py`
- Create: `src/evoeventmem/infra/async_in_memory_repository.py`
- Create: `src/evoeventmem/infra/async_embedding.py`
- Create: `tests/infra/test_async_repository_contract.py`
- Create: `tests/infra/test_async_embedding.py`
- Create: `tests/api/test_request_scope.py`

- [ ] **Step 1: Add failing scope model tests**

Require nonempty tenant and user, optional session narrowing, stable canonical
serialization, and explicit scope/body mismatch. Missing tenant/user is invalid.

- [ ] **Step 2: Add failing async port contract tests**

Define scoped `add`, `get`, `update`, `list`, vector search, ping/schema status,
and close behavior. Freeze the production embedding boundary in the same task:
an additive async embedding port exposes declared `model_id`, `dimension`, and
async query/document embedding; a typed vector carries those fields and finite
numeric values; repository write receives the document vector separately from
the durable `MemoryRecord`; `search_vector` receives a query vector plus
scope/limit. All UUID lookups require `RequestScope`. Preserve the existing
synchronous `MemoryRepository` and domain `MemoryRecord` unchanged.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/infra/test_async_repository_contract.py \
  tests/infra/test_async_embedding.py tests/api/test_request_scope.py -x
```

Expected: async protocol, scope model, and async fake are absent.

- [ ] **Step 4: Implement only the contract and async fake**

The fake enforces the same isolation/vector-dimension rules expected from
PostgreSQL. Add a deterministic async embedding fake for service/contract tests.
It is a test/development adapter, not an automatic production fallback.

- [ ] **Step 5: Verify and commit contract-only work**

```bash
uv run pytest -q tests/infra/test_async_repository_contract.py \
  tests/infra/test_async_embedding.py tests/api/test_request_scope.py
uv run mypy src
git add src/evoeventmem/core/ports.py \
  src/evoeventmem/infra/async_in_memory_repository.py \
  src/evoeventmem/infra/async_embedding.py \
  tests/infra/test_async_repository_contract.py \
  tests/infra/test_async_embedding.py \
  tests/api/test_request_scope.py
git commit -m "feat(core): freeze scoped async production ports"
```

Handoff for `F0`. Do not begin feature work against an unmerged contract.

### Task D2: Extract Shared Pure Memory Rules

**Files:**

- Create: `src/evoeventmem/services/memory_rules.py`
- Create: `tests/services/test_memory_rules.py`
- Modify: `src/evoeventmem/services/memory_service.py`
- Modify: existing sync service tests only as necessary

- [ ] **Step 1: Add characterization tests around existing sync behavior**

Cover collision/idempotency identity, scope consistency, feedback transitions,
forget behavior, and no cross-scope existence leak. Run them before moving code.

- [ ] **Step 2: Verify the characterization baseline**

```bash
uv run pytest -q tests/services/test_write_pipeline.py \
  tests/test_memory_service.py
```

Expected: existing tested domain behavior passes; record inherited failures.

- [ ] **Step 3: Add failing pure-function tests**

Tests call rules without repository/API dependencies and assert deterministic
results for the same inputs.

- [ ] **Step 4: Move only reusable decisions into `memory_rules.py`**

Both sync and future async services call the same pure functions. Do not create
a second memory algorithm or refactor unrelated research code.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/services/test_memory_rules.py \
  tests/services/test_write_pipeline.py tests/test_memory_service.py
git add src/evoeventmem/services/memory_rules.py \
  src/evoeventmem/services/memory_service.py \
  tests/services/test_memory_rules.py tests/services/test_write_pipeline.py \
  tests/test_memory_service.py
git commit -m "refactor(service): share pure memory business rules"
```

### Task D3: Replace the Event-Loop Thread with an Async Pool

**Files:**

- Rewrite: `src/evoeventmem/infra/postgres_repository.py`
- Modify: `tests/infra/test_async_repository_contract.py`
- Modify: `tests/infra/test_postgres_repository.py`
- Create/modify: `tests/infra/conftest.py`

- [ ] **Step 1: Add pool/concurrency/scope RED tests**

Require `asyncpg.create_pool`, acquire per operation, concurrent operations
without a dedicated thread/shared connection, tenant/user/optional-session SQL
filters on every get/update/list operation, stable timeout errors, and async
close.

- [ ] **Step 2: Add fail-not-skip PostgreSQL fixture behavior**

Register `postgres` cases. With `EEM_REQUIRE_POSTGRES=1`, missing DSN or failed
connection calls `pytest.fail`, never `pytest.skip`. The default CPU suite may
exclude the marker but cannot claim PG acceptance.

- [ ] **Step 3: Verify local RED without PostgreSQL**

```bash
uv run pytest -q tests/infra/test_async_repository_contract.py \
  -m 'not postgres' -x
```

Expected: old sync facade/thread does not implement the async contract.

- [ ] **Step 4: Implement the async repository**

Remove `threading.Thread`, `run_coroutine_threadsafe`, and shared `_conn` from
the production path. Use pool acquire and `asyncio.timeout`. Never interpolate
scope values into SQL.

- [ ] **Step 5: Verify local tests and commit**

```bash
uv run pytest -q tests/infra/test_async_repository_contract.py \
  tests/infra/test_postgres_repository.py -m 'not postgres'
git add src/evoeventmem/infra/postgres_repository.py \
  tests/infra/conftest.py tests/infra/test_async_repository_contract.py \
  tests/infra/test_postgres_repository.py
git commit -m "feat(postgres): add pooled scoped async repository"
```

Real PG tests wait for the Integration Lead's approved Compose gate.

### Task D4: Add Versioned pgvector Migrations and Readiness

**Files:**

- Modify: `src/evoeventmem/infra/migrations.py`
- Optional create: `src/evoeventmem/infra/sql/0001_core.sql`
- Optional create: `src/evoeventmem/infra/sql/0002_pgvector.sql`
- Modify: `tests/infra/test_postgres_repository.py`
- Modify: `src/evoeventmem/infra/config.py`

- [ ] **Step 1: Add migration/readiness RED tests**

Require vector extension, embedding storage with model ID/dimension, one
configured index dimension, idempotent migrations, schema metadata, readiness
failure on model/dimension/schema mismatch, and dimension-invalid write
rejection.

- [ ] **Step 2: Verify the intended failure against an approved test DB**

After the Lead starts PostgreSQL:

```bash
EEM_REQUIRE_POSTGRES=1 \
DATABASE_URL=postgresql://evoeventmem:evoeventmem@127.0.0.1:5432/evoeventmem \
uv run pytest -q tests/infra/test_postgres_repository.py -m postgres -x
```

Expected before implementation: missing extension/vector/schema metadata.

- [ ] **Step 3: Implement additive versioned migrations**

Never auto-drop/alter existing non-test vectors. Encountering incompatible
existing data is a stop condition. Keep migrations idempotent and ordered.

- [ ] **Step 4: Verify twice and commit**

Run the PG command twice. Expected: first applies pending versions; second
applies none and passes.

```bash
git add src/evoeventmem/infra/migrations.py \
  src/evoeventmem/infra/config.py tests/infra/test_postgres_repository.py
# Run this additional add only if the optional SQL files were created:
git add src/evoeventmem/infra/sql/0001_core.sql \
  src/evoeventmem/infra/sql/0002_pgvector.sql
git commit -m "feat(postgres): add versioned pgvector schema"
```

### Task D5: Implement Actual Scoped pgvector Search

**Files:**

- Modify: `src/evoeventmem/infra/postgres_repository.py`
- Modify: `src/evoeventmem/infra/async_embedding.py`
- Modify: `src/evoeventmem/infra/config.py`
- Create: `tests/infra/test_pgvector_search.py`
- Modify: `tests/infra/test_async_embedding.py`
- Modify: production embedding port/adapter only through an approved D-owned
  path implementing D1's frozen async embedding contract; request an interface
  change if that frozen contract proves insufficient

- [ ] **Step 1: Add deterministic vector ordering tests**

Insert controlled vectors and require cosine ordering, active-status filtering,
tenant/user/session isolation, source=`pgvector`, raw/vector score
decomposition, and explicit fallback state.

- [ ] **Step 2: Add index-use smoke**

Use `EXPLAIN` to prove a vector-capable path without brittle exact-plan text.
Do not treat Python token overlap as a vector implementation.

Add mocked-transport tests for the concrete
`AsyncOpenAICompatibleEmbeddingModel` in
`src/evoeventmem/infra/async_embedding.py`: configured endpoint/model/dimension,
query/document calls, dimension mismatch, timeout, and redacted stable failure.
The same module retains an explicitly named deterministic development adapter
for Compose smoke; it is never selected implicitly.

- [ ] **Step 3: Verify RED against approved PostgreSQL**

```bash
EEM_REQUIRE_POSTGRES=1 \
DATABASE_URL=postgresql://evoeventmem:evoeventmem@127.0.0.1:5432/evoeventmem \
uv run pytest -q tests/infra/test_pgvector_search.py -m postgres -x
```

Expected: no vector column/query API in current WIP.

- [ ] **Step 4: Implement parameterized scoped vector SQL**

Use the configured embedding dimension/model. Python token overlap may remain
only under an explicit `development_token_overlap` policy whose response,
health, log, and metric all show degradation.

Implement the async OpenAI-compatible production embedding adapter in
`infra/async_embedding.py`, configure its provider/base URL/API-key env name,
model, dimension, and timeout in `infra/config.py`, and pass typed vectors to
the repository. Never serialize the key or log vector values.

- [ ] **Step 5: Verify and commit**

```bash
EEM_REQUIRE_POSTGRES=1 \
DATABASE_URL=postgresql://evoeventmem:evoeventmem@127.0.0.1:5432/evoeventmem \
uv run pytest -q tests/infra/test_pgvector_search.py \
  tests/infra/test_async_repository_contract.py -m postgres
git add src/evoeventmem/infra/postgres_repository.py \
  src/evoeventmem/infra/async_embedding.py src/evoeventmem/infra/config.py \
  tests/infra/test_pgvector_search.py tests/infra/test_async_embedding.py
git commit -m "feat(postgres): search scoped candidates with pgvector"
```

### Task D6: Add the Async Application Service and Required Scope API

**Files:**

- Create: `src/evoeventmem/services/async_memory_service.py`
- Create: `tests/services/test_async_memory_service.py`
- Modify: `src/evoeventmem/api/app.py`
- Modify: `tests/api/test_api_endpoints.py`
- Modify: `tests/api/test_request_scope.py`

- [ ] **Step 1: Add async service RED tests**

Test scoped write/search/explain/feedback/forget, shared pure rules, awaited
repository calls, timeouts, and indistinguishable not-found/wrong-scope results.

- [ ] **Step 2: Add API scope RED tests**

Freeze concrete tenant/user header names and optional session header. Missing
headers fail; body/query identity mismatch fails; every UUID lookup requires
scope; handlers are async.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/services/test_async_memory_service.py \
  tests/api/test_request_scope.py tests/api/test_api_endpoints.py -x
```

Expected: current endpoints are synchronous and accept optional query scope.

- [ ] **Step 4: Implement FastAPI scope dependency and async service**

The API calls only the async application service. Preserve legacy response
adapters where contractually required, but never bypass scope for compatibility.
Wire the configured production/development async embedding adapter into the
service explicitly. Readiness reports the selected provider/model/dimension;
embedding failures flow through D1's typed port and D7's stable reason codes.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/services/test_async_memory_service.py \
  tests/api/test_request_scope.py tests/api/test_api_endpoints.py \
  tests/test_api.py
git add src/evoeventmem/services/async_memory_service.py \
  src/evoeventmem/api/app.py tests/services/test_async_memory_service.py \
  tests/api/test_request_scope.py tests/api/test_api_endpoints.py
git commit -m "feat(api): enforce scope through async application service"
```

### Task D7: Fail Closed and Make Failures Observable

**Files:**

- Modify: `src/evoeventmem/api/app.py`
- Modify: `src/evoeventmem/infra/config.py`
- Modify: `src/evoeventmem/infra/logging.py`
- Modify: `src/evoeventmem/infra/metrics.py`
- Modify: `tests/api/test_api_endpoints.py`
- Modify: `tests/infra/test_infra_metrics.py`
- Modify: `tests/infra/test_logging.py`

- [ ] **Step 1: Add fail-closed RED tests**

PostgreSQL unavailable means readiness/write 503 by default. Only an explicitly
configured development fallback may write, and response/health/log/metric all
identify degradation.

- [ ] **Step 2: Add bounded observability RED tests**

DB/embedding/source failures record request ID, stable reason code, source, and
duration. Metrics use route templates such as
`/v1/memories/{memory_id}/feedback`, not raw UUID paths, and count exception
responses as well as successes. Logs never include DSNs/secrets/raw text/vector.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest -q tests/api/test_api_endpoints.py \
  tests/infra/test_infra_metrics.py tests/infra/test_logging.py -x
```

Expected: WIP silently serves writes from memory, uses raw paths, and misses
some exception metrics.

- [ ] **Step 4: Implement explicit failure policy**

Middleware records in `try/except/finally`; route template labels come from the
matched route. Bound all metric labels. Do not log payloads.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q tests/api tests/infra/test_infra_metrics.py \
  tests/infra/test_logging.py
git add src/evoeventmem/api/app.py src/evoeventmem/infra/config.py \
  src/evoeventmem/infra/logging.py src/evoeventmem/infra/metrics.py \
  tests/api tests/infra/test_infra_metrics.py tests/infra/test_logging.py
git commit -m "feat(api): fail closed with bounded failure telemetry"
```

### Task D8: Stabilize OpenAPI and Compose Smoke

**Files:**

- Modify: `scripts/generate_openapi.py`
- Modify: `api/openapi.json`
- Modify: `scripts/compose_smoke.py`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: production tests as needed

- [ ] **Step 1: Add deterministic OpenAPI test/check**

Generate twice and require no diff. Scope headers, error responses, and all
write/search/explain/feedback/forget/health/readiness/metrics endpoints must be
present.

- [ ] **Step 2: Extend smoke expectations without starting Docker**

The script must send required scope headers and check migration/readiness,
scoped write, pgvector search, explain, feedback, forget, metrics, persistence
after restart, and a negative cross-scope request. Do not mount Docker socket
inside the smoke container. Implement host-callable `--phase seed` and
`--phase verify` modes with an explicit `--state-file`: seed writes only test
identifiers; the Lead restarts the API externally; verify reloads the identifiers
and proves persistence before forget. Keep a one-shot mode for the Compose
profile.

- [ ] **Step 3: Verify local generation/config only**

```bash
uv run python scripts/generate_openapi.py
git diff --exit-code -- api/openapi.json
docker compose config
```

Expected: generation is stable; Compose parses. `docker compose config` does
not start services.

- [ ] **Step 4: Stop for Compose approval**

Present exact cwd/command, image/build/volume effects, expected outputs, and
cleanup/retry notes. Do not run `up` in this worker.

- [ ] **Step 5: Commit static deployment work**

```bash
git add Dockerfile docker-compose.yml api/openapi.json \
  scripts/generate_openapi.py scripts/compose_smoke.py
git commit -m "chore(production): stabilize OpenAPI and Compose smoke"
```

### Task D9: Run Gate E After Lead Approval

The Integration Lead, not D, runs:

```bash
docker compose up -d --build postgres api
uv run python scripts/compose_smoke.py \
  --base-url http://127.0.0.1:8000 \
  --phase seed \
  --state-file artifacts/smoke/compose-state.json
docker compose restart api
uv run python scripts/compose_smoke.py \
  --base-url http://127.0.0.1:8000 \
  --phase verify \
  --state-file artifacts/smoke/compose-state.json
EEM_REQUIRE_POSTGRES=1 \
DATABASE_URL=postgresql://evoeventmem:evoeventmem@127.0.0.1:5432/evoeventmem \
uv run pytest -q \
  tests/infra/test_async_repository_contract.py \
  tests/infra/test_postgres_repository.py \
  tests/infra/test_pgvector_search.py \
  -m postgres -ra
docker compose --profile smoke up --build \
  --abort-on-container-exit --exit-code-from smoke
```

Acceptance:

- passed PostgreSQL test count is nonzero;
- skipped count is zero;
- pgvector ordering/scope/readiness tests pass;
- Compose smoke includes persistence and cross-scope negatives;
- no silent fallback event exists;
- OpenAPI remains unchanged after generation.

If any integration failure reproduces, the Lead returns the exact trace to D;
D adds a failing focused test before the fix and makes a new owned commit.

## Workstream D Handoff

Report:

- baseline `B0`, freeze `F0`, and D commit SHAs;
- exact changed paths and any Lead dependency request;
- RequestScope/async-port/schema/model/dimension versions;
- local unit test results;
- approved PostgreSQL/Compose results, including pass/skip counts;
- pgvector query source/score/fallback evidence;
- scope isolation, failure reason codes, and metric label inventory;
- OpenAPI hash and Compose config result;
- migration/non-test-data risks;
- confirmation that D did not edit research paths, start unapproved Docker,
  run GPU/live models, or silently fall back.
