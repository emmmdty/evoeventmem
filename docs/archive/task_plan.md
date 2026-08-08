# Task Plan: M11-M16 Remediation Specification

## Goal

Produce an OpenCode-ready, dependency-aware multi-subagent specification and implementation plan that repairs the verified M11-M16 engineering, methodology, experiment, analysis, and production gaps without duplicate ownership.

## Current Phase

Phase 5

## Phases

### Phase 1: Requirements & Discovery

- [x] Confirm multi-subagent execution is allowed and preferred
- [x] Confirm related work may remain in one continuous workstream
- [x] Inventory the affected modules, tests, artifacts, and dirty worktree
- **Status:** complete

### Phase 2: Design Specification

- [x] Define workstream boundaries and exclusive file ownership
- [x] Define dependency gates, experiment validity rules, and integration protocol
- [x] Write the design spec
- [x] Run the spec review loop
- **Status:** complete

### Phase 3: User Review Gate

- [x] Ask the user to review the written design spec
- [x] Incorporate requested changes and re-review if needed
- **Status:** complete

### Phase 4: Implementation Plan

- [x] Convert the approved design into an OpenCode-ready implementation plan
- [x] Include exact files, TDD steps, commands, expected failures, commits, and subagent prompts
- [x] Run the plan review loop
- **Status:** complete

### Phase 5: Delivery

- [x] Verify the spec and plan are complete and internally consistent
- [x] Hand off execution instructions to the user
- **Status:** complete

## Key Questions

1. How can shared runner/retrieval interfaces have one owner while dependent agents continue useful parallel work?
2. Which gates separate code correctness, clean benchmark execution, claim generation, and production readiness?
3. How should OpenCode coordinate experiments that require credentials or remote GPU services without starting them automatically?

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Use four dependency-aware workstreams plus an integration lead | Groups continuous fixes by subsystem and avoids one-agent-per-milestone duplication |
| Give each shared file exactly one owning workstream | Prevents concurrent edits and duplicated fixes |
| Gate final M15 claims on clean M13/M14 artifacts | Analysis must not stabilize claims from obsolete or methodologically invalid runs |
| Keep GPU/live runs as explicit user-approved gates | Repository instructions prohibit automatic heavy or credentialed execution |
| Do not modify business code during specification work | The user requested a repair spec for later OpenCode execution |
| Split delivery into one master plan plus four subsystem plans | Keeps each continuous worker buildable and avoids a 2,500-line single-agent checklist |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| Patch context included a decision row from the wrong tracking file | 1 | Re-read `task_plan.md` and applied a file-specific patch |
| Multi-file ablation approval patch used a stale heading context | 1 | Re-read the exact master-plan section and applied a narrower patch |

## Notes

- Preserve the user's existing dirty M15/M16 worktree; planning documents must not absorb or revert those changes.
- The final plan must make it impossible for two subagents to independently repair the same provider, retrieval, provenance, or report path.
