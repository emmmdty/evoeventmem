# Stage 1a 独立审查报告

**结论**: PASS

## Implementation summary

The implementer added four optional ETEC fact fields (`fact_slot`, `fact_value`, `valid_from`, `valid_until`) to `_EventDraft` in `src/evoeventmem/extraction.py`, bumped `LLMEventExtractor.PROMPT_VERSION` to `event-extraction.v2`, extended `_build_llm_prompt` with a schema block + `fact_slot_rules` + four `fact_slot_examples` (including a two-event state-change pair), and rewired `_build_memory` to mirror fact metadata into `metadata[]` AND set `MemoryRecord.valid_from`/`valid_to` so the consolidator's existing `_fact_effective_time` (consolidation.py:770) and `_interval` (consolidation.py:916) pick them up — closing R1b without touching consolidation. Eleven new unit tests cover prompt advertisement, draft normalization, fact-contract invariants, metadata propagation for both start- and end-events, metadata omission when absent, an end-to-end SUPERSEDE logical-reachability test, and a v1-style negative control. No consolidation/retrieval/router/benchmark code was modified; R3 (`multi_valued`) is intentionally untouched; no LLM quota was spent.

## §1-10 acceptance checklist

| # | Check | Result | Command output |
|---|---|---|---|
| 1 | `grep -cE "fact_slot\|valid_from\|valid_until\|fact_value" src/evoeventmem/extraction.py` ≥ 4 | ✅ | `110` (real field defs + assignments, verified) |
| 2 | `uv run pytest tests/consolidation tests/retrieval -q` green | ✅ | `176 passed in 0.21s` |
| 3 | `uv run ruff check .` green | ✅ | `All checks passed!` |
| 4 | `uv run mypy src` green | ✅ | `Success: no issues found in 33 source files` |
| 5 | `uv run python -m evoeventmem.cli smoke` prints "smoke ok" | ✅ | `smoke ok: The project switched the package registry to npmmirror. score=0.400` |
| 6 | `git diff src/evoeventmem/consolidation.py` empty (no `multi_valued`/`0.7`/`supersede_contradiction_min`) | ✅ | empty diff; grep `exit=1` (no matches) |
| 7 | `git diff src/evoeventmem/retrieval.py src/evoeventmem/router.py` empty | ✅ | empty diff (both files) |
| 8 | `git status --short runs/` no new artifacts | ✅ | no output (runs/ is gitignored; pre-existing `runs/mechanism/etec_stress/etec-stress-20260817T093946Z/` dated Aug 17, before today Aug 19) |
| 9 | `LLMEventExtractor.PROMPT_VERSION == "event-extraction.v2"` | ✅ | `706:    PROMPT_VERSION = "event-extraction.v2"` |
| 10 | `uv run pytest tests/consolidation tests/retrieval tests/extraction tests/benchmarks/test_etec_stress.py -q` all pass | ✅ | `216 passed in 0.38s` |

Note on §1 item 10 / scope-down: the slow E2E benchmark suites `tests/benchmarks/test_locomo_run.py` and `tests/benchmarks/test_longmemeval_run.py` were excluded because they are out of S1a scope (they consume LLM quota and measure empirical SUPERSEDE trigger rate, which is S2's job). The scoped suite covers exactly the four suites S1a impacts: consolidation logic, retrieval/provenance, extraction, and the ETEC stress fixture.

## §A-E critical findings

### §A. Spec Step 4 literal vs implementation gap

**Finding**: The spec's Step 4 literal code only wrote `valid_from`/`valid_until` into `metadata`. The implementer went beyond this and also set `MemoryRecord.valid_from`/`valid_to`. This was NECESSARY, not overreach.

**Evidence**:
- `fact_slot_key` (consolidation.py:935-940) reads `memory.metadata.get("fact_slot")` → for the R1 gate (`_same_fact_slot`), the **metadata mirror IS functional**.
- `_fact_effective_time` (consolidation.py:770-771) reads `memory.valid_from` (the **record attribute**), NOT metadata.
- `_interval` (consolidation.py:916-921) reads `memory.valid_from` and `memory.valid_to` (the **record attributes**), NOT metadata.

Therefore the spec's literal Step 4 code (metadata-only for `valid_from`/`valid_until`) would close R1 (fact_slot) but leave R1b OPEN, because the consolidator never reads `metadata["valid_from"]`. The implementer correctly identified this gap and set both: `MemoryRecord.valid_from = valid_from` (functional, closes R1b) AND `metadata["valid_from"] = valid_from.isoformat()` (auditability only — the consolidator does not read this from metadata).

The code comment at extraction.py:1210-1215 explicitly documents this rationale ("Mirror fact metadata into ``metadata`` for auditability and write the temporal bounds onto the record's ``valid_from``/``valid_to`` so the consolidator's existing ``_fact_effective_time``/``_interval`` (consolidation.py:770, :917) close R1b without any consolidation change").

**Recommendation**: None — interpretation is defensible and necessary to satisfy the spec's stated goal ("让 `_fact_effective_time` 拿到 `valid_from`"). Future stages should be aware that `metadata["valid_from"]` is auditability-only; functional reads come from the record attribute.

### §B. State-change two-event split semantics

**Finding**: The prompt's few-shot example matches the spec contract, and `_EventDraft.model_validator` accepts each event of the split independently.

**Evidence**:
- Prompt example (extraction.py:902-927): `turn_1_event` has `fact_slot="profile.city"`, `fact_value="Seattle"`, `valid_from="2023-01-15..."`, `valid_until="2023-06-01..."`; `turn_2_event` has SAME `fact_slot`, DIFFERENT `fact_value="Portland"`, `valid_from="2023-06-01..."` (same boundary), `valid_until=None`. Exactly the contract the spec Step 2 docstring requires.
- `_EventDraft._enforce_fact_contract` (extraction.py:303-322): end-event (`fact_slot`+`fact_value`+`valid_from`+`valid_until`) passes; start-event (`fact_slot`+`fact_value`+`valid_from`+`valid_until=None`) passes. The validator does NOT reject the two-event split.
- `test_event_draft_enforces_fact_contract_invariants` (test_event_extraction.py:768-777) explicitly constructs and validates the end-event draft.

**Recommendation**: None.

### §C. End-to-end SUPERSEDE reachability test

**Finding**: The test is a fair logical-reachability check, NOT an empirical SUPERSEDE>0 claim.

**Evidence**:
- `test_fact_extraction_chain_reaches_supersede_on_real_extraction_output` (test_event_extraction.py:927-1027) uses `StaticJSONChatModel` with hand-crafted v2 LLM output (two same-slot `profile.city`, different-value Seattle/Portland events, both with `valid_from` set, `valid_until=None`).
- Both intervals are open-ended → overlap → `_contradiction_score` = `0.6 + 1.0*0.2 + 0.8*0.2 = 0.96` ≥ 0.7 → SUPERSEDE. Test asserts `contradiction_score >= 0.7` and `action is ConsolidationAction.SUPERSEDE`. Verified PASS in isolation.
- Docstring (lines 928-936) explicitly disclaims empirical measurement: *"It does NOT measure empirical SUPERSEDE trigger rate on LongMemEval data, which is the S2 stage."*
- The inline comment (lines 1013-1016) says *"the contradiction score formula at consolidation.py:886 can reach >=0.7"* — a logical reachability statement (the formula CAN reach it given these inputs), not an empirical claim.
- `grep -nE "显著提升|significant improvement|outperform|SUPERSEDE 可达|SUPERSEDE > 0|supersede reachable" src/evoeventmem/extraction.py` → no matches (`exit=1`).

**Recommendation**: None.

### §D. Negative control

**Finding**: The v1-style negative control correctly keeps `contradiction_score == 0.0` and not SUPERSEDE.

**Evidence**:
- `test_extraction_without_fact_slot_does_not_supersede` (test_event_extraction.py:1030-1112) uses `StaticJSONChatModel` with v1-style output (no `fact_slot`/`fact_value`/`valid_from`/`valid_until` on either event).
- Asserts `"fact_slot" not in candidate.memory.metadata`, `candidate.memory.valid_from is None`, `second_decision.decision.action is not ConsolidationAction.SUPERSEDE`, `second_decision.decision.features.contradiction_score == 0.0`. Verified PASS in isolation.
- Confirms the v2 schema change does not create spurious SUPERSEDE on events that omit fact metadata.

**Recommendation**: None.

### §E. Spec scope compliance

**Finding**: No changes exceed S1a scope. No R3 fix attempt.

**Evidence**:
- `git diff --stat`:
  ```
  src/evoeventmem/extraction.py             | 254 ++++++++++++++++-
  tests/extraction/test_event_extraction.py | 459 ++++++++++++++++++++++++++++++
  2 files changed, 703 insertions(+), 10 deletions(-)
  ```
  Only 2 files modified — `src/evoeventmem/extraction.py` and `tests/extraction/test_event_extraction.py`.
- `git diff src/evoeventmem/consolidation.py` = EMPTY.
- `git diff src/evoeventmem/retrieval.py src/evoeventmem/router.py` = EMPTY.
- `git diff src/evoeventmem/consolidation.py | grep -E "multi_valued|0\.7|supersede_contradiction_min"` → no matches (`exit=1`).
- `grep -n "multi_valued" src/evoeventmem/extraction.py` → only 2 matches, both in a code COMMENT (lines 1214-1215) noting R3 is out of scope. NO `_EventDraft.multi_valued` field added. The existing `_memory_is_multi_valued` (consolidation.py:984-985) on metadata is untouched and not in scope.
- No retrieval/router/benchmark/configs changes.

**Recommendation**: None.

## Additional verification (spec review protocol §2-10)

### §2. Schema landing (real field defs/assignments)

`grep -nE "fact_slot|valid_from|valid_until|fact_value" src/evoeventmem/extraction.py` returns 110 occurrences. Real field definitions and assignments (not docstrings/comments):

```
275:    fact_slot: str | None = None
276:    fact_value: str | None = None
277:    valid_from: datetime | None = None
278:    valid_until: datetime | None = None
...
807:                        fact_slot=draft.fact_slot,
808:                        fact_value=draft.fact_value,
809:                        valid_from=draft.valid_from,
810:                        valid_until=draft.valid_until,
...
1198:    fact_slot: str | None = None,
1199:    fact_value: str | None = None,
1200:    valid_from: datetime | None = None,
1201:    valid_until: datetime | None = None,
...
1216:    if fact_slot is not None:
1217:        metadata["fact_slot"] = fact_slot
1218:    if fact_value is not None:
1219:        metadata["fact_value"] = fact_value
1220:    if valid_from is not None:
1221:        metadata["valid_from"] = valid_from.isoformat()
1222:    if valid_until is not None:
1223:        metadata["valid_until"] = valid_until.isoformat()
...
1234:        valid_from=valid_from,
1235:        valid_to=valid_until,
```

### §3. Fixture behavior unchanged (4/12 SUPERSEDE preserved)

`uv run pytest tests/benchmarks/test_etec_stress.py -v` → 13 passed. The action-stratified test `test_stress_run_is_action_stratified_with_nonzero_merge_and_supersede` PASS.

Programmatic run of the stress fixture confirms:
```
action_counts: {'merge': 3, 'supersede': 4, 'add': 4, 'reject': 1}
supersede_count: 4
merge_count: 3
case_count: 12
```

The 4 SUPERSEDE cases (all `action_match=True`, `contradiction_score=0.96`, `multi_valued=False`, `supersede_contradiction_min: 0.7` unchanged):
1. `stress_newer_supersedes_older` → SUPERSEDE ✅
2. `stress_stale_incoming_historical` → SUPERSEDE ✅
3. `stress_conflicting_evidence` → SUPERSEDE ✅
4. `stress_cross_session_consolidation` → SUPERSEDE ✅

`action_accuracy == 1.0` across all 12 cases. Fixture behavior unchanged.

### §4. Provenance unbroken

`grep -n "evidence_refs\|raw_turn_id\|locator" src/evoeventmem/extraction.py` confirms the evidence chain in `_build_memory` is intact:
- Line 1232: `evidence_refs=list(evidence_refs),` (preserved, no overwrite)
- Line 456: `"raw_turn_id": turn.turn_id,` (in `_turn_evidence.metadata`)
- Lines 424, 452, 480, 488: `locator=...` (event-summary/turn/observation evidence)

`uv run pytest tests/retrieval/test_qemr.py -q` → `48 passed in 0.09s`. Provenance chain intact.

### §5. R3 untouched

- `git diff src/evoeventmem/consolidation.py` = EMPTY.
- `git diff src/evoeventmem/consolidation.py | grep -E "multi_valued|0\.7|supersede_contradiction_min"` → `exit=1` (no matches).
- `grep -n "multi_valued" src/evoeventmem/extraction.py` → only 2 matches in a code COMMENT (lines 1214-1215) noting R3 is out of scope. NO `_EventDraft.multi_valued` field added.

### §6. Scope boundary

`git diff --stat` (quoted in §E above) — only `src/evoeventmem/extraction.py` (+254/-10) and `tests/extraction/test_event_extraction.py` (+459/-0). No other files touched.

### §7. No new run artifacts

- `git status --short runs/` → no output (runs/ is gitignored).
- `ls runs/mechanism/etec_stress/` → only `etec-stress-20260817T093946Z` (dated Aug 17, before today Aug 19; pre-existing). No fresh benchmark artifacts created during this session.

### §8. No new overclaim

`grep -rnE "显著提升|significant improvement|outperform|SUPERSEDE 可达|SUPERSEDE > 0|supersede reachable" docs/ src/` → matches only in pre-existing spec/audit/review text:
- `docs/S1a-execution-prompt.md` (lines 9, 31, 217, 245) — the spec's OWN anti-overclaim rule text ("不声称 SUPERSEDE > 0", "S1a 后 SUPERSEDE 仍可能 = 0").
- `docs/8of10_AUDIT.md`, `docs/REMEDIATION_SPEC.md`, `docs/STRONG_RESULTS_SMALL_SAMPLE.md`, `docs/STAGE0_REVIEW.md`, `docs/8of10_ACCEPTANCE.md` — all pre-existing spec/audit/review text.

`grep -nE "显著提升|significant improvement|outperform|SUPERSEDE 可达|SUPERSEDE > 0|supersede reachable" src/evoeventmem/extraction.py` → `exit=1` (NO matches in the implementer's NEW source content). The implementer introduced no new overclaim phrases.

### §9. Git state

`git status --short`:
```
 M src/evoeventmem/extraction.py
 M tests/extraction/test_event_extraction.py
```
Only the 2 files in §6 are modified. HEAD:
```
8545635 docs(s1a): execution prompt for ETEC R1/R1b schema + prompt v2
b60b38d docs(s0): honesty disclosure — test50-mimo, drop 9/10→8/10, fix baseline framing
e231576 docs(s0): add Stage 0 execution prompt for new-window handoff
b2d8942 docs(spec): remediation spec v1.1 (PASS, 6 stages) + 2 independent reviews
e585d7e chore(benchmarks): O09 mechanism probes + 50-run MiMo V2.5 config and launcher
```
HEAD = `8545635` (the docs(s1a) execution-prompt commit). The implementer did NOT commit code changes — only the S1a execution-prompt doc commit at HEAD is present, which is allowed per spec.

### §10. AGENTS.md boundary

- **No vendor-specific model client**: `src/evoeventmem/extraction.py:12` imports only `from evoeventmem.core.ports import ChatMessage, ChatModel` — vendor-neutral port. No OpenAI/Anthropic/Google/etc. client imports.
- **UTC-aware datetimes**: `_EventDraft.parse_event_time` (extraction.py:280-291) parses ISO-8601 with offset via `datetime.fromisoformat(repaired.replace("Z", "+00:00"))`; `_parse_event_time` (extraction.py:1283-1297) constructs `datetime(..., tzinfo=UTC)`. Tests use `datetime(2023, 6, 1, tzinfo=UTC)`. All UTC-aware.
- **Small pure functions + ports**: `_build_memory`, `_EventDraft._enforce_fact_contract`, `_EventDraft._normalize_fact_field` are small pure functions. `LLMEventExtractor` depends on the `ChatModel` port, not a concrete vendor client. `MemoryRecord.valid_from`/`valid_to` (domain/models.py:114-115) with validator `valid_to must not be earlier than valid_from` (line 167-168) pre-existed.

## Risk register

1. **R1 (low)**: `metadata["valid_from"]`/`metadata["valid_until"]` are auditability-only — the consolidator reads `valid_from`/`valid_to` from the record ATTRIBUTE, not metadata. If a future stage reads `metadata["valid_from"]` expecting it to drive consolidation, it would be a silent no-op. Mitigation: the code comment at extraction.py:1210-1215 documents this; future stages must read the record attribute.
2. **R2 (low, expected)**: S1a does NOT fix R3 (`multi_valued` over-flagging). Per spec, S1a after SUPERSEDE may still = 0 on real LongMemEval data because the LLM may continue to over-flag `multi_valued=True`. This is explicitly in scope-out and is NOT an S1a failure — S2 will measure the empirical trigger rate.
3. **R3 (low)**: The end-to-end SUPERSEDE reachability test uses `DeterministicFakeEmbeddingModel` and `StaticJSONChatModel` with hand-crafted LLM output. This is a logical-reachability test, not an empirical measurement on real data. The test docstring explicitly disclaims this; the inline comment uses "can reach" not "does reach".
4. **R4 (low)**: The prompt's few-shot state-change example emits TWO events, but real LLMs may simplify state changes to a single event. S1a's schema and validator accept both forms (the single-event form sets `valid_from` and leaves `valid_until=None`); S2 will measure the empirical simplification rate. Not an S1a failure.
5. **R5 (low, pre-existing)**: The smoke command output includes `score=0.400` and "The project switched the package registry to npmmirror" — this is the pre-existing smoke behavior, unrelated to S1a. The spec only requires "smoke ok" to print, which it does.

## Sign-off

- The implementer did NOT commit code changes. `git status` shows only the 2 modified working-tree files (`src/evoeventmem/extraction.py`, `tests/extraction/test_event_extraction.py`), both unstaged.
- HEAD is at `8545635` (`docs(s1a): execution prompt for ETEC R1/R1b schema + prompt v2`), which is the only S1a doc commit and is allowed per spec ("不擅自 commit").
- All 10 acceptance criteria pass, all §A-E critical findings are sound, and the risk register holds only low-severity expected risks (R3 unfix is in-scope-out by design).
- Verdict: **PASS** — S1a is complete and the project may proceed to S1b (5-question real-data smoke extraction + reachability tests) after the implementer commits the working-tree changes with the spec's commit-message template `feat(s1a): ETEC R1/R1b schema — fact_slot/valid_from/valid_until/fact_value in extraction + prompt v2`.
