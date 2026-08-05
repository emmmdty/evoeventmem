# M11-M16 Remediation Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the verified M11-M16 method, benchmark, evidence, analysis,
and production gaps through four persistent subsystem workers and one
Integration Lead without task-ID agents or duplicate fixes.

**Architecture:** Freeze shared contracts from one reviewed WIP baseline, then
run Workstreams A, B, C, and D in isolated worktrees with exclusive ownership.
Merge through explicit handoffs: B stress cases before A consolidation work, A
retrieval policy before B final experiments, and B finalized source runs before
C final claims. Production work remains independent after its scope/async-port
contract freezes.

**Tech Stack:** Python 3.11+, uv, Pydantic v2, pytest, FastAPI, asyncpg,
PostgreSQL 16/pgvector, Docker Compose, Ruff, mypy, TOML/JSONL artifacts.

---

## Plan Set

The Integration Lead reads all five documents before dispatching workers:

1. This master plan.
2. `docs/superpowers/plans/2026-08-05-m11-m16-workstream-a-retrieval.md`
3. `docs/superpowers/plans/2026-08-05-m11-m16-workstream-b-benchmarks.md`
4. `docs/superpowers/plans/2026-08-05-m11-m16-workstream-c-analysis.md`
5. `docs/superpowers/plans/2026-08-05-m11-m16-workstream-d-production.md`

Normative design:

- `docs/superpowers/specs/2026-08-05-m11-m16-remediation-design.md`

The subsystem plans are continuous work packages. Do not replace them with six
independent M11-M16 agents.

## Non-Negotiable Rules

- Never reset, stash, clean, delete, or overwrite the existing dirty M15/M16
  work.
- Never run local GPU training or inference.
- Never start SSH, remote inference, a live model request, publication run, or
  `docker compose ... up` without the explicit approval packets in Tasks 5-6.
- `vector_rag` indexes normalized raw-turn chunks only. Only event-memory
  methods consume the shared extraction snapshot.
- Every durable and packed memory retains exact source provenance under both
  evidence policies.
- Workstream A implements method controls; B configures and executes them; C
  validates and analyzes finalized outputs.
- Finalized source runs are read-only. C writes only below
  `artifacts/analysis/<analysis_id>/`.
- Workers do not merge, edit `TASKS.md`/`tasks/index.json`, or modify another
  worker's paths.

## Dependency Graph

```text
reviewed WIP baseline B0
        |
contract-only A + B + D, then C consumer test
        |
frozen registry commit F0
        |
        +--> A router/QEMR/budget -----------+
        |                                    |
        +--> B providers/provenance/stress --+--> B final smoke/ablations
        |                 |                  |             |
        |                 +--> A ETEC fixes -+             +--> publication runs
        |                                                        |
        +--> C synthetic framework ------------------------------+--> final report
        |
        +--> D async PostgreSQL/pgvector/API --------------------------> Gate E

all branches + clean code gates + approved artifacts -----------------> release
```

At most three feature workers run beside the Integration Lead. Recommended
waves:

1. A, B, and D start from `F0`.
2. When B hands off the stress commit, pause B and start C.
3. A completes stress-authorized ETEC fixes; B resumes after A's retrieval
   policy freeze.
4. C generates final reports only after both B publication runs finalize.

## Exclusive Ownership

| Role | Owned paths |
|---|---|
| Integration Lead | `AGENTS.md`, `TASKS.md`, `tasks/index.json`, project docs, `docs/contracts/m11_m16_remediation.md`, `pyproject.toml`, merge/integration notes |
| A | router/retrieval/consolidation/tokenization modules, retrieval and consolidation tests/fixtures, retrieval smoke |
| B | benchmark common/LongMemEval/LoCoMo/experiments, benchmark configs, extraction, benchmark/extraction tests, source-run artifacts |
| C | `benchmarks/analysis/`, `tests/analysis/`, `configs/analysis/`, content-addressed analysis artifacts |
| D | API, infra, production services/ports, production tests, Docker/Compose, OpenAPI scripts/artifact |

If a worker needs a foreign file, it stops that step and submits:

```text
Interface request
- requester branch/base:
- owning workstream:
- contract/path:
- required change:
- consumer impact:
- focused failing contract test:
- work that can continue without the change:
```

### Task 0: Verify the Approved Plan Set Is Tracked

**Files:** the five plan files listed under "Plan Set".

- [ ] **Step 1: Verify every worker plan is present in Git**

```bash
git ls-files --error-unmatch \
  docs/superpowers/plans/2026-08-05-m11-m16-remediation-master-plan.md \
  docs/superpowers/plans/2026-08-05-m11-m16-workstream-a-retrieval.md \
  docs/superpowers/plans/2026-08-05-m11-m16-workstream-b-benchmarks.md \
  docs/superpowers/plans/2026-08-05-m11-m16-workstream-c-analysis.md \
  docs/superpowers/plans/2026-08-05-m11-m16-workstream-d-production.md
git log -1 --format='%H %s' -- docs/superpowers/plans
```

Expected: all five paths are tracked by the approved plan-set commit. Record
that commit as `P0`. If any plan is untracked, stop and ask approval to commit
only the five plan files before creating a branch/worktree. The exact fallback
scope is:

```bash
git add -- \
  docs/superpowers/plans/2026-08-05-m11-m16-remediation-master-plan.md \
  docs/superpowers/plans/2026-08-05-m11-m16-workstream-a-retrieval.md \
  docs/superpowers/plans/2026-08-05-m11-m16-workstream-b-benchmarks.md \
  docs/superpowers/plans/2026-08-05-m11-m16-workstream-c-analysis.md \
  docs/superpowers/plans/2026-08-05-m11-m16-workstream-d-production.md
git diff --cached --check
git diff --cached --stat
git commit -m "docs: plan M11-M16 remediation execution"
```

Run the commit only after showing that exact scope and receiving approval.
Never copy an untracked plan into worktrees by shell commands.

### Task 1: Preserve the Dirty WIP Baseline

**Files to preserve after review:**

- M15 WIP: `benchmarks/analysis/`, `tests/analysis/`
- M16 tracked WIP: `Dockerfile`, `docker-compose.yml`, `pyproject.toml`,
  `src/evoeventmem/api/app.py`, `src/evoeventmem/core/ports.py`,
  `src/evoeventmem/infra/in_memory_repository.py`,
  `src/evoeventmem/services/memory_service.py`
- M16 untracked WIP: `api/openapi.json`, `scripts/compose_smoke.py`,
  `scripts/generate_openapi.py`, `src/evoeventmem/infra/config.py`,
  `src/evoeventmem/infra/logging.py`, `src/evoeventmem/infra/metrics.py`,
  `src/evoeventmem/infra/migrations.py`,
  `src/evoeventmem/infra/postgres_repository.py`, `tests/api/`, and the
  production files below `tests/infra/`
- Never stage the internal planning files `task_plan.md`, `findings.md`, or
  `progress.md` in the WIP baseline.

- [ ] **Step 1: Create a preservation branch without changing files**

Run:

```bash
git switch -c remediation/m11-m16-wip-baseline
git status --short
git diff --stat
git diff --check
git ls-files --others --exclude-standard
```

Expected: the known mixed M15/M16 WIP is listed; no whitespace error is
introduced by this command.

- [ ] **Step 2: Record inherited verification**

Run:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
```

Expected baseline: tests may show the already-known PostgreSQL skips, but no
new failure is hidden. Record exact pass/fail/skip counts; never describe
skipped PostgreSQL tests as passing integration.

- [ ] **Step 3: Stage only the reviewed WIP scope**

Run:

```bash
git add -- \
  Dockerfile docker-compose.yml pyproject.toml \
  src/evoeventmem/api/app.py \
  src/evoeventmem/core/ports.py \
  src/evoeventmem/infra/in_memory_repository.py \
  src/evoeventmem/services/memory_service.py \
  api/openapi.json benchmarks/analysis \
  scripts/compose_smoke.py scripts/generate_openapi.py \
  src/evoeventmem/infra/config.py \
  src/evoeventmem/infra/logging.py \
  src/evoeventmem/infra/metrics.py \
  src/evoeventmem/infra/migrations.py \
  src/evoeventmem/infra/postgres_repository.py \
  tests/analysis tests/api \
  tests/infra/test_infra_metrics.py \
  tests/infra/test_logging.py \
  tests/infra/test_postgres_repository.py \
  tests/infra/test_repository_contract.py
git diff --cached --check
git diff --cached --stat
git status --short
```

Expected: the cache contains only reviewed M15/M16 WIP. Planning files remain
untracked.

- [ ] **Step 4: Stop for baseline commit approval**

Show the user the exact staged paths, diff summary, verification results, and
these inherited defects: LoCoMo-only mutable analysis output, synchronous
thread-backed PostgreSQL facade, no pgvector query, optional scope, and silent
in-memory fallback. Ask approval for exactly:

```bash
git commit -m "chore: preserve reviewed M15-M16 WIP baseline"
```

Do not continue to parallel worktrees without approval.

- [ ] **Step 5: Record `B0` and create the integration worktree**

After approval, commit and run:

```bash
git rev-parse HEAD
git status --short
git worktree add ../evoeventmem-integration \
  -b remediation/m11-m16-integration \
  remediation/m11-m16-wip-baseline
```

Expected: one WIP checkpoint commit and a clean integration worktree on a new
branch at that commit. Record its SHA as `B0`. Run all remaining Lead commands
from `../evoeventmem-integration`. If approval is denied, all feature edits must
run sequentially in the current tree; subagents may do only read-only work.

- [ ] **Step 6: Provision ignored datasets as read-only inputs**

Git worktrees do not copy ignored `data/raw`. Verify the original licensed data
root, then create an untracked symlink for the integration worktree:

```bash
test -d /home/tjk/myProjects/internship-projects/evoeventmem-starter/data/raw
mkdir -p /home/tjk/myProjects/internship-projects/evoeventmem-integration/data
test ! -e /home/tjk/myProjects/internship-projects/evoeventmem-integration/data/raw
ln -s /home/tjk/myProjects/internship-projects/evoeventmem-starter/data/raw \
  /home/tjk/myProjects/internship-projects/evoeventmem-integration/data/raw
git -C /home/tjk/myProjects/internship-projects/evoeventmem-integration \
  check-ignore data/raw
```

Expected: configs resolve the same files and hashes. Treat the target as
read-only by policy; never stage the symlink, dataset, or generated cache.

### Task 2: Freeze Cross-Workstream Contracts

**Files:**

- Create (Lead): `docs/contracts/m11_m16_remediation.md`
- Create (A): `src/evoeventmem/tokenization.py`
- Modify (A): `src/evoeventmem/retrieval.py`
- Modify (B): `benchmarks/common/artifacts.py`
- Modify (D): `src/evoeventmem/core/ports.py`
- Create/modify contract tests named in the four workstream plans
- Modify (Lead only, if required): `pyproject.toml`

- [ ] **Step 1: Draft the registry at `B0`**

For each contract, write: ID/version, owner, implementation path,
producer/consumers, exact fields and invariants, canonical serialization/hash,
compatibility policy, test path/command, consumer-impact note, and eventual
implementation commit.

Commit the candidate registry so every contract worktree sees the same input:

```bash
git add docs/contracts/m11_m16_remediation.md
git commit -m "docs: draft M11-M16 contract registry"
```

- [ ] **Step 2: Dispatch contract-only A, B, and D workers**

From the integration worktree, use `superpowers:using-git-worktrees` and run:

```bash
git worktree add ../evoeventmem-contract-a \
  -b remediation/contract-a remediation/m11-m16-integration
git worktree add ../evoeventmem-contract-b \
  -b remediation/contract-b remediation/m11-m16-integration
git worktree add ../evoeventmem-contract-d \
  -b remediation/contract-d remediation/m11-m16-integration
```

Each worker writes only its contract and focused tests, uses TDD, commits once,
and stops. Do not begin feature behavior yet.

- [ ] **Step 3: Merge the three owner commits into the integration branch**

From `../evoeventmem-integration`, review file ownership and run:

```bash
git merge --no-ff remediation/contract-a \
  -m "merge: freeze retrieval contracts"
git merge --no-ff remediation/contract-b \
  -m "merge: freeze benchmark artifact contracts"
git merge --no-ff remediation/contract-d \
  -m "merge: freeze production port contracts"
```

Then verify:

```bash
uv run pytest -q \
  tests/retrieval/test_tokenization.py \
  tests/retrieval/test_retrieval_contract.py
uv run pytest -q tests/benchmarks/test_artifact_contract.py
uv run pytest -q \
  tests/infra/test_async_repository_contract.py \
  tests/infra/test_async_embedding.py \
  tests/api/test_request_scope.py
```

Expected: all frozen producer contract tests pass.

- [ ] **Step 4: Add C's consumer compatibility test**

Create a branch from the now-merged integration branch:

```bash
git worktree add ../evoeventmem-contract-c \
  -b remediation/contract-c remediation/m11-m16-integration
```

Dispatch C only to add `tests/analysis/test_artifact_contract.py` against B's
merged artifact schema. C must not redefine a parallel schema. After its
contract-only commit, merge it from the integration worktree:

```bash
git merge --no-ff remediation/contract-c \
  -m "merge: freeze analysis artifact consumers"
```

Run:

```bash
uv run pytest -q tests/analysis/test_artifact_contract.py
```

Expected: C validates the B-owned models and hashes without writing a source
run.

- [ ] **Step 5: Finalize registry and dependencies**

The Lead alone applies approved dependency edits to `pyproject.toml`, updates
the registry with exact owner commit SHAs and contract versions, then runs:

```bash
uv sync --extra dev --extra models --extra postgres --extra bench
uv run pytest -q tests/retrieval/test_retrieval_contract.py \
  tests/benchmarks/test_artifact_contract.py \
  tests/analysis/test_artifact_contract.py \
  tests/infra/test_async_repository_contract.py \
  tests/infra/test_async_embedding.py \
  tests/api/test_request_scope.py
uv run ruff check .
uv run mypy src
```

Expected: contract tests and static checks pass. If `uv sync` needs network
downloads, tell the user before running it.

- [ ] **Step 6: Commit `F0`**

```bash
git add docs/contracts/m11_m16_remediation.md pyproject.toml
git commit -m "docs: freeze M11-M16 remediation contracts"
git rev-parse HEAD
```

Record the resulting SHA as `F0`. Every feature branch starts from this exact
commit. Any later contract change is a stop condition.

### Task 3: Create Feature Worktrees and Dispatch Persistent Workers

- [ ] **Step 1: Create one clean worktree per subsystem from `F0`**

Use explicit sibling paths, never the repository root or home directory:

```bash
git worktree add ../evoeventmem-ws-a \
  -b remediation/ws-a remediation/m11-m16-integration
git worktree add ../evoeventmem-ws-b \
  -b remediation/ws-b remediation/m11-m16-integration
git worktree add ../evoeventmem-ws-c \
  -b remediation/ws-c remediation/m11-m16-integration
git worktree add ../evoeventmem-ws-d \
  -b remediation/ws-d remediation/m11-m16-integration
```

Expected: each worktree is clean and points to `F0`.

Provision B's ignored dataset input without copying it:

```bash
mkdir -p /home/tjk/myProjects/internship-projects/evoeventmem-ws-b/data
test ! -e /home/tjk/myProjects/internship-projects/evoeventmem-ws-b/data/raw
ln -s /home/tjk/myProjects/internship-projects/evoeventmem-starter/data/raw \
  /home/tjk/myProjects/internship-projects/evoeventmem-ws-b/data/raw
git -C /home/tjk/myProjects/internship-projects/evoeventmem-ws-b \
  check-ignore data/raw
```

- [ ] **Step 2: Dispatch A, B, and D**

Use the shared prompt and role-specific payload in Appendix A. Point each
worker at its worktree and workstream plan. Tell workers they are not alone and
must not revert others' edits. Contract tasks A1, B1, C1, and D1 are already
merged in `F0`; feature workers verify those focused tests and start at A2, B2,
and D2. C later verifies C1 and starts at C2.

- [ ] **Step 3: Track handoffs, not chat/task IDs**

For every worker commit record:

```text
workstream/branch/base:
owned commit(s):
changed files:
tests with exact results:
schema/policy versions:
artifact paths and hashes:
interface requests:
remaining risks:
live/remote/Docker actions: not run | approved command/result
```

- [ ] **Step 4: Enforce dependency pauses**

- B sends its predeclared ETEC stress commit and traces before A edits
  `consolidation.py`.
- A freezes retrieval policy/control versions before B finishes dataset runners
  or executes ablations.
- C may build against synthetic artifacts after B's schema contract, but it
  cannot produce final claims before both publication runs finalize.

### Task 4: Merge in Dependency Order

- [ ] **Step 1: Merge B's provider/provenance/stress preparation**

Review B-owned files, smoke/unit outputs, fixture hash, expected case IDs, and
MERGE/SUPERSEDE action evidence. Merge only after B's focused gates pass.

From the integration worktree:

```bash
git diff --name-only \
  remediation/m11-m16-integration...remediation/ws-b
git merge --no-ff remediation/ws-b \
  -m "merge: add benchmark providers provenance and ETEC stress"
uv run pytest -q tests/extraction \
  tests/benchmarks/test_artifact_contract.py \
  tests/benchmarks/test_artifacts.py \
  tests/benchmarks/test_providers.py \
  tests/benchmarks/test_memory_inputs.py \
  tests/benchmarks/test_etec_stress.py
```

Expected: the name list contains only B-owned paths and focused B preparation
tests pass after merge.

- [ ] **Step 2: Give B's stress handoff to A**

A either fixes reproduced consolidation failures with A-owned regression tests
or records that all predeclared cases already pass. A may not tune thresholds
from LoCoMo outcome metrics.

Make B's exact fixture/harness available to A without copying files:

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-ws-a
git merge --no-ff remediation/m11-m16-integration \
  -m "merge: consume benchmark stress handoff"
```

Expected: A sees B's committed stress fixture/test at the reviewed SHA and does
not edit B-owned paths.

- [ ] **Step 3: Merge A's method branch**

Run Gate A from the A plan. Record router/retrieval/consolidation policy
versions and estimator version. This commit is B's only method-policy input.

After review, merge A from the integration worktree:

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-integration
git diff --name-only \
  remediation/m11-m16-integration...remediation/ws-a
git merge --no-ff remediation/ws-a \
  -m "merge: repair router retrieval budget and ETEC behavior"
uv run pytest -q tests/retrieval tests/consolidation \
  tests/benchmarks/test_retrieval_smoke.py \
  tests/benchmarks/test_etec_stress.py
uv run python -m benchmarks.retrieval_smoke
```

Expected: the pre-merge list contains only A-owned changes (plus B paths already
merged unchanged for stress consumption), and Gate A passes after merge.

- [ ] **Step 4: Rebase/merge the A policy into B, then finish B**

B resolves consumer changes without editing A internals, completes both smoke
runners and controlled ablations, and hands off source artifact schemas and
proposed main commands.

After A is merged into integration, B consumes that exact policy via:

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-ws-b
git merge --no-ff remediation/m11-m16-integration \
  -m "merge: consume frozen retrieval policy"
```

After B completes B5-B8 and passes Gate B, merge its final branch:

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-integration
git diff --name-only \
  remediation/m11-m16-integration...remediation/ws-b
git merge --no-ff remediation/ws-b \
  -m "merge: finalize fair benchmark and ablation runners"
uv run pytest -q tests/extraction tests/benchmarks
uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/smoke.toml
uv run python -m benchmarks.locomo.run \
  --config configs/locomo/smoke.toml
```

Expected: only B-owned final changes appear and both deterministic runners pass.

- [ ] **Step 5: Merge C framework, then D**

C must pass synthetic gates without source artifacts. D must pass local tests;
D must also pass Task 5's separately approved early PostgreSQL no-skip gate
before merge. Final API-image/restart Compose readiness remains pending until
the integrated gate. Resolve `pyproject.toml` only in the Lead branch.

First review all dependency requests. The Lead applies only approved dependency
changes in the integration worktree and commits them before feature merges:

```bash
cd /home/tjk/myProjects/internship-projects/evoeventmem-integration
uv sync --extra dev --extra models --extra postgres --extra bench
git add pyproject.toml uv.lock
git commit -m "build: align remediation runtime dependencies"
```

If neither file changed, skip that commit. Then merge C and D:

```bash
git diff --name-only \
  remediation/m11-m16-integration...remediation/ws-c
git diff --name-only \
  remediation/m11-m16-integration...remediation/ws-d
git merge --no-ff remediation/ws-c \
  -m "merge: add immutable two-dataset analysis"
git merge --no-ff remediation/ws-d \
  -m "merge: add scoped async pgvector production service"
uv sync --extra dev --extra models --extra postgres --extra bench
uv run pytest -q tests/analysis tests/api tests/infra tests/services
uv run ruff check .
uv run mypy src
```

Expected: dependency lock and all four merges are explicit; PostgreSQL-marked
tests are not claimed by this CPU command and retain Task 5's no-skip evidence.
The two name lists must be limited to C-owned and D-owned paths respectively.

- [ ] **Step 6: Produce clean integrated code commit `R0`**

Run the non-live, non-Docker gates in Task 7. Commit only after they pass and
record `R0`. Publication configs must resolve to `R0`, a clean tree, and frozen
hashes.

### Task 5: PostgreSQL Development and Final Compose Approval Gates

These are two separate approvals. Early database tests unblock D4/D5 before D
can commit/merge; the final Compose gate runs only after D is integrated.

- [ ] **Step 1: Present the early D-branch database approval packet**

Working directory:

```text
/home/tjk/myProjects/internship-projects/evoeventmem-ws-d
```

Proposed commands:

```bash
docker compose config
docker compose up -d postgres
```

Explain that this creates/starts only the test PostgreSQL service and volume.
Run only after explicit user approval. D then runs the exact RED/GREEN command
inside D4; after D5 creates `tests/infra/test_pgvector_search.py`, it runs D5's
expanded no-skip command. Do not reference that not-yet-created test before D5.
Connection failure must fail, not skip.

- [ ] **Step 2: Merge D only after the approved no-skip database gate**

Require a nonzero passed count, zero skips, idempotent migrations, scoped CRUD,
and pgvector ordering. Record the Compose project/worktree and exact results in
D's handoff. Then, under the same early approval packet, stop/remove the D
worktree's containers/network without deleting its volume so port 5432 is free:

```bash
docker compose down
```

Verify `docker compose ps` is empty. The early approval does not authorize the
final API image/smoke, and `docker compose down -v` is forbidden.

- [ ] **Step 3: Present the final integrated Compose approval packet**

Working directory:

```text
/home/tjk/myProjects/internship-projects/evoeventmem-integration
```

Proposed commands:

```bash
docker compose config
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

Explain image builds and container/volume effects. `seed` performs scoped write,
pgvector search, explain, feedback, metrics, and negative cross-scope checks and
stores only test IDs. After the Lead restarts the API container, `verify`
confirms readiness, persisted search/explain state, forget, and metrics. The
profile smoke then rechecks the complete one-shot protocol. No Docker socket is
mounted into a test container.

- [ ] **Step 4: Run final Compose only after its separate approval**

Expected: migration/readiness, scoped pgvector path, persistence after API
restart, failure observability, and negative isolation all pass. The PostgreSQL
pytest runs while detached services are still available and records a nonzero
passed count with zero skips. The final one-shot profile may stop its dependent
services after smoke exits; no later step assumes they remain running.

### Task 6: Live Publication and Ablation Approval Gate

- [ ] **Step 1: Validate configs without network calls**

```bash
uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/main.toml --validate-config
uv run python -m benchmarks.locomo.run \
  --config configs/locomo/main.toml --validate-config
uv run python -m benchmarks.experiments.ablation \
  --config configs/ablations/controlled.toml \
  --run-dir runs/validation/controlled-ablations
```

Expected: resolved reader, extractor, embedding, tokenizer, policies, budgets,
dataset hash, clean `R0`, and output class are printed with secrets redacted.
The deterministic controlled run is regenerated from `R0`, makes no network
calls, finalizes in the integration worktree, and records a nonzero decision
delta for every required factor. Record its `FINALIZED.json` hash.

- [ ] **Step 2: Stop and present one approval packet per run**

Each packet must include exact command, cwd, env variable names only,
provider/model IDs, endpoint/approved SSH host, config hash, clean commit,
output directory, duration/cost class, expected files, and resume command.
Current `gpu-5090` config comments conflict with the repository's `gpu-4090`
default; ask the user which endpoint is authoritative and do not guess.

- [ ] **Step 3: Run approved LongMemEval Small**

Proposed command:

```bash
uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/main.toml \
  --run-dir runs/publication/longmemeval
```

Environment names: the resolved reader, extractor, and embedding API key env
names printed by config validation. Expected final artifact:
`runs/publication/longmemeval/FINALIZED.json`.

- [ ] **Step 4: Run approved LoCoMo**

```bash
uv run python -m benchmarks.locomo.run \
  --config configs/locomo/main.toml \
  --run-dir runs/publication/locomo
```

Expected final artifact: `runs/publication/locomo/FINALIZED.json`.

- [ ] **Step 5: Present separate approval packets for dataset ablations**

After both base runs finalize, show exact ablation commands, changed-factor
matrix, fixed reader/extractor/embedding/budgets, expected additional model
calls/cache hits, output directories, duration/cost, and resume behavior. A
base-run approval does not automatically authorize its ablations.

First run read-only base/factor validation:

```bash
uv run python -m benchmarks.experiments.ablation \
  --config configs/ablations/longmemeval.toml \
  --base-run runs/publication/longmemeval \
  --controlled-run runs/validation/controlled-ablations \
  --validate-config
uv run python -m benchmarks.experiments.ablation \
  --config configs/ablations/locomo.toml \
  --base-run runs/publication/locomo \
  --controlled-run runs/validation/controlled-ablations \
  --validate-config
```

Expected: base finalization hash, single-factor pairs, fixed fields, A policy
version, and controlled-activation reference validate without model calls.

- [ ] **Step 6: Run approved LongMemEval and LoCoMo ablations**

```bash
uv run python -m benchmarks.experiments.ablation \
  --config configs/ablations/longmemeval.toml \
  --base-run runs/publication/longmemeval \
  --controlled-run runs/validation/controlled-ablations \
  --run-dir runs/publication/ablations/longmemeval
uv run python -m benchmarks.experiments.ablation \
  --config configs/ablations/locomo.toml \
  --base-run runs/publication/locomo \
  --controlled-run runs/validation/controlled-ablations \
  --run-dir runs/publication/ablations/locomo
```

Expected: each directory contains paired single-factor manifests,
per-question rows, controlled-activation references, binding-budget flags, and
`FINALIZED.json`.

- [ ] **Step 7: Resume only with an identical manifest**

```bash
uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/main.toml \
  --resume-dir runs/publication/longmemeval
uv run python -m benchmarks.locomo.run \
  --config configs/locomo/main.toml \
  --resume-dir runs/publication/locomo
uv run python -m benchmarks.experiments.ablation \
  --config configs/ablations/longmemeval.toml \
  --base-run runs/publication/longmemeval \
  --controlled-run runs/validation/controlled-ablations \
  --resume-dir runs/publication/ablations/longmemeval
uv run python -m benchmarks.experiments.ablation \
  --config configs/ablations/locomo.toml \
  --base-run runs/publication/locomo \
  --controlled-run runs/validation/controlled-ablations \
  --resume-dir runs/publication/ablations/locomo
```

Expected: completed sample files are untouched. Any manifest drift is a hard
failure. Never delete completed artifacts automatically.

### Task 7: Final Analysis and Repository Gates

- [ ] **Step 1: Generate and validate the content-addressed report**

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

Expected: both commands derive the same `analysis_id` from two base, one
controlled, and two dataset-ablation finalization hashes plus the analysis
config; all source hashes/mtimes are unchanged; the analysis directory has its
own `FINALIZED.json`.
Compatibility must pass within each dataset/comparison family. Different
LongMemEval and LoCoMo model stacks are reported separately and are not a
cross-dataset incompatibility or paired comparison.

- [ ] **Step 2: Run repository-wide CPU verification**

```bash
uv sync --extra dev --extra models --extra postgres --extra bench
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run python -m evoeventmem.cli smoke
uv run python -m benchmarks.retrieval_smoke
uv run python -m benchmarks.longmemeval.run \
  --config configs/longmemeval/smoke.toml
uv run python -m benchmarks.locomo.run \
  --config configs/locomo/smoke.toml
```

Expected: no failure; skips are enumerated and none are required PostgreSQL
gates already claimed in Task 5.

- [ ] **Step 3: Re-run the approved PostgreSQL/Compose gate if required**

Use Task 5's exact commands and approval scope. Do not infer renewed approval
for a materially changed image/config.

- [ ] **Step 4: Reconcile task metadata only from evidence**

The Lead updates `TASKS.md` and `tasks/index.json` only for milestones whose
gates and required artifact hashes exist. Do not mark benchmark gains or main
experiments complete from smoke/diagnostic outputs.

- [ ] **Step 5: Commit final metadata and risk register**

```bash
git add TASKS.md tasks/index.json docs/ARCHITECTURE.md docs/EVALUATION.md
git commit -m "docs: record validated M11-M16 remediation status"
```

Expected: documentation values link to real run IDs/config hashes and an
explicit unresolved-risk list.

## Appendix A: OpenCode Subagent Prompts

### Contract Worker A Prompt

```text
You are the contract-only Workstream A agent in ../evoeventmem-contract-a.
Other agents are active; do not revert or edit their paths. Read AGENTS.md, the
approved design, candidate registry, and only Task A1 of the A plan.

Using TDD, create TokenEstimator and the retrieval request/result/control
schema plus focused tests in A-owned paths. Provenance is mandatory in both
evidence policies. Do not implement temporal ranking, fusion, smoke, benchmark,
or production behavior. Do not edit registry or pyproject.toml.

Run A1's exact tests/mypy, commit exactly once, and hand off commit SHA,
changed paths, contract version/fields/invariants, consumer impact, and exact
results. Stop after A1.
```

### Contract Worker B Prompt

```text
You are the contract-only Workstream B agent in ../evoeventmem-contract-b.
Other agents are active; do not revert or edit their paths. Read AGENTS.md, the
approved design, candidate registry, and only Task B1 of the B plan.

Using TDD, implement B-owned RunManifest, AblationRunManifest, extraction and
artifact records, canonical hashing, working/finalized state, and focused
contract tests. Do not implement providers, runners, extraction behavior,
experiments, analysis, or production. Do not edit registry or pyproject.toml.

Run B1's exact tests, commit exactly once, and hand off commit SHA, schema
versions, required paths/hashes, compatibility policy, consumer impact, and
exact results. Stop after B1.
```

### Contract Worker D Prompt

```text
You are the contract-only Workstream D agent in ../evoeventmem-contract-d.
Other agents are active; do not revert or edit their paths. Read AGENTS.md, the
approved design, candidate registry, and only Task D1 of the D plan.

Using TDD, add RequestScope, additive async repository and embedding ports,
typed model/dimension vectors, async in-memory/embedding fakes, and focused
scope/contract tests. Preserve the synchronous research repository and domain
MemoryRecord. Do not implement PostgreSQL, API, migrations, observability, or
Compose. Do not edit registry or pyproject.toml.

Run D1's exact tests/mypy, commit exactly once, and hand off commit SHA,
contract fields/invariants, query/write embedding flow, consumer impact, and
exact results. Stop after D1.
```

### Contract Worker C Prompt

```text
You are the contract-only Workstream C agent in ../evoeventmem-contract-c,
created only after B's producer contract is merged. Other agents are active;
do not revert or edit their paths. Read AGENTS.md, the approved design,
candidate registry, and only Task C1 of the C plan.

Using TDD, add B-schema consumer compatibility tests and C's dataset-neutral
AnalysisRow model. Import B's models; do not redefine producer schemas or build
loaders/reports/statistics. Do not edit source runs, registry, or pyproject.toml.

Run C1's exact test, commit exactly once, and hand off commit SHA, consumer
impact/gaps, and exact results. Stop after C1.
```

### Integration Lead Prompt

```text
You are the Integration Lead for the approved M11-M16 remediation design and
master plan. Use superpowers:subagent-driven-development and
superpowers:using-git-worktrees. Do not implement feature code.

Read AGENTS.md, TASKS.md, docs/CODEX_WORKFLOW.md, the approved design, this
master plan, all four workstream plans, and the frozen contract registry.

First inventory and preserve the dirty WIP. Never reset, stash, clean, delete,
or overwrite it. Do not commit it without showing exact staged scope, tests,
known defects, and receiving explicit user approval. Record B0 and F0.

Create all feature worktrees from F0. Coordinate persistent subsystems A/B/C/D,
never task-ID-specific M11...M16 repair agents. Enforce exclusive ownership.
Workers do not merge, edit task metadata, or patch foreign contracts.

Track commit IDs, interface requests, test evidence, artifact hashes, risks,
and handoffs. Enforce B stress before A ETEC, A method freeze before B final
runners/ablations, and finalized B artifacts before C final reports.

Never run live providers, SSH/GPU jobs, publication runs, destructive
migrations, or Docker Compose up without the exact approval packet in this
plan. Never publish a gain without clean, finalized, hash-verified artifacts.
```

### Shared Worker Preamble

```text
You are one persistent subsystem worker in a multi-agent remediation. Other
workers are active. Do not revert, reformat, merge, or edit their files. Work
only in your assigned clean worktree, owned paths, and stated upstream commit.

Read AGENTS.md, the approved remediation design, frozen contract registry, and
your one workstream plan. Use test-driven-development. Do not spawn separate
M11/M12/etc. repair agents or duplicate another subsystem's work.

Frozen contracts may not change. Stop and submit an interface request with
consumer impact and a focused failing contract test. Do not edit TASKS.md,
tasks/index.json, Lead-owned docs, or pyproject.toml.

Do not commit datasets, secrets, model weights, generated caches, private
traces, or publication artifacts. Do not run live/network models, SSH/GPU,
remote commands, destructive migrations, or Docker Compose up.

Handoff with base/commit IDs, changed files, exact command results,
schema/policy versions, artifact hashes, remaining risks, and interface
requests. Explicitly state which live/remote/Docker actions were not run.
```

### Workstream A Payload

```text
Follow docs/superpowers/plans/2026-08-05-m11-m16-workstream-a-retrieval.md.
Task A1 is already merged in F0: verify it, then start A2.
Own only its listed method/retrieval/consolidation paths. Implement temporal
semantics, relevance-first retrieval, observable fallback, deterministic RRF,
complete reader-message budgets, and provenance-preserving controls. Preserve
fixed-vector behavior. Do not edit benchmark runners/configs, analysis,
production, artifact schemas, or pyproject.toml. Do not change ETEC until B's
exact stress commit reproduces a failure. Hand off the frozen method versions
and focused test outputs to B.
```

### Workstream B Payload

```text
Follow docs/superpowers/plans/2026-08-05-m11-m16-workstream-b-benchmarks.md.
Task B1 is already merged in F0: verify it, then start B2.
Own benchmark providers, normalized inputs, extraction, runners, experiments,
configs, tests, and source-run artifacts. vector_rag indexes raw-turn chunks
only; event methods alone share one extraction snapshot. Preserve exact
provenance and keep structural targets out of extractor input. Do not edit A
method internals, C analysis, D production, or pyproject.toml. Produce stress
reproduction before requesting A consolidation work. Wait for A's policy
freeze before final runner/ablation work. Prepare but never start live runs.
```

### Workstream C Payload

```text
Follow docs/superpowers/plans/2026-08-05-m11-m16-workstream-c-analysis.md.
Task C1 is already merged in F0: verify it, then start C2.
Own analysis code/tests/config and artifacts/analysis only. Read finalized B
source runs but never write inside them or execute source methods. Build
dataset-neutral validation, paired/Holm statistics, factor-isolation analysis,
dynamic claims, and deterministic review sampling. Before B publication
artifacts exist, use synthetic fixtures only. Reject legacy runs/main/report,
inactive-control claims, hard-coded metrics, and incomplete two-dataset input.
```

### Workstream D Payload

```text
Follow docs/superpowers/plans/2026-08-05-m11-m16-workstream-d-production.md.
Task D1 is already merged in F0: verify it, then start D2.
Own production API/infra/services/ports/tests/Compose/OpenAPI paths. Preserve
the synchronous research repository contract and share only pure rules. Build
required RequestScope, async pool-backed PostgreSQL, scoped pgvector search,
fail-closed production behavior, stable migrations/schema, and bounded
observability. Never use an event-loop thread, shared single connection,
unscoped UUID lookup, silent volatile fallback, or token-overlap as production
search. Request pyproject changes from the Lead. Do not start Compose.
```

## Completion Definition

This master plan is complete only when:

- every workstream's focused tests pass;
- all stress IDs pass and MERGE/SUPERSEDE execute;
- both publication source runs are complete, clean, immutable, and hash-verified;
- every required ablation changes a controlled decision and budget settings bind;
- C's two-dataset report is content-addressed and source runs remain unchanged;
- PostgreSQL tests execute with zero unexpected skips;
- pgvector/scope/failure/Compose gates pass with user approval;
- final repository tests/lint/types/smokes pass; and
- task metadata reflects evidence rather than intended status.
