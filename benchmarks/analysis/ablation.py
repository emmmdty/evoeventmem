"""Artifact-only ablation analysis (C5): analyze, never execute.

Workstream C consumes B's finalized ablation families and emits structured
factor results. It never executes retrieval, extraction, or consolidation
internals and never imports method/runner modules: everything below is read
from the finalized ``retrieval.jsonl`` rows, manifests, and ``deltas.json``.

Rules (Gate D):

- Paired manifests may differ in exactly the declared factor
  (``evidence_policy``, ``temporal_source``, ``graph_source``, ``routing``,
  ``weights``, ``budget``); reader, extractor, embedding, dataset, budgets
  except the tested one, caps, and policies stay fixed. Row-level payload
  differences must stay within the declared factor's observable fields.
- Every required switch must have ``decision_delta_count > 0`` on the
  controlled fixture. A publication dataset with zero row delta is labeled
  ``no_observed_dataset_effect`` — neither an implementation failure nor a
  positive effect.
- Budget experiments require two or more settings, and publication questions
  must be marked ``packing_bound=true`` at two or more of those settings.
- Factor results are retrieval-trace diagnostics (``metric_kind`` is always
  ``retrieval_proxy``); they can never be presented as end-to-end QA gains.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from benchmarks.analysis.loaders import LoadedAblationArm, LoadedAblationRun
from benchmarks.common.artifacts import canonical_json_hash

KNOWN_FACTORS: tuple[str, ...] = (
    "evidence_policy",
    "temporal_source",
    "graph_source",
    "routing",
    "weights",
    "budget",
)

# Payload fields whose differences encode the observable decision of each
# factor. An arm whose rows differ outside these fields leaks its factor.
FACTOR_FIELDS: dict[str, frozenset[str]] = {
    "evidence_policy": frozenset({"evidence_policy"}),
    "temporal_source": frozenset({"exclusions", "exclusion_count"}),
    "graph_source": frozenset({"exclusions", "exclusion_count"}),
    "routing": frozenset({"intent"}),
    "weights": frozenset({"packed_items"}),
    "budget": frozenset({"budget_tokens", "packing_bound", "exclusions", "exclusion_count"}),
}

# Payload fields that encode retrieval decisions (delta fingerprint).
FINGERPRINT_FIELDS: tuple[str, ...] = (
    "intent",
    "strategy",
    "evidence_policy",
    "budget_tokens",
    "total_tokens",
    "content_tokens",
    "prompt_overhead_tokens",
    "total_input_tokens_estimate",
    "packing_bound",
    "candidate_count",
    "exclusion_count",
    "exclusions",
    "source_failures",
    "packed_items",
)

# Payload fields that are bookkeeping, not retrieval decisions.
_DIAGNOSTIC_FIELDS = frozenset({"dataset", "sample_id", "question_id", "arm", "method"})


@dataclass(frozen=True)
class FactorResult:
    """Structured result for one declared ablation factor of one family."""

    factor: str
    arm_names: tuple[str, ...]
    decision_delta_count: int
    controlled_active: bool | None
    status: Literal["active", "no_observed_dataset_effect", "inactive"]
    metric_kind: Literal["retrieval_proxy"] = "retrieval_proxy"
    packing_bound_questions: dict[str, int] = field(default_factory=dict)
    budget_settings: tuple[int, ...] = ()
    issues: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "arm_names": list(self.arm_names),
            "decision_delta_count": self.decision_delta_count,
            "controlled_active": self.controlled_active,
            "status": self.status,
            "metric_kind": self.metric_kind,
            "packing_bound_questions": dict(self.packing_bound_questions),
            "budget_settings": list(self.budget_settings),
            "issues": list(self.issues),
        }


def _rows_by_question(arm: LoadedAblationArm) -> dict[str, dict[str, Any]]:
    return {str(row["question_id"]): row for row in arm.rows}


def _fingerprint(row: Mapping[str, Any]) -> str:
    subset = {field: row.get(field) for field in FINGERPRINT_FIELDS}
    return canonical_json_hash(subset)


def _differing_fields(base: Mapping[str, Any], arm_row: Mapping[str, Any]) -> list[str]:
    return sorted(
        field_name
        for field_name in set(base) | set(arm_row)
        if field_name not in _DIAGNOSTIC_FIELDS and base.get(field_name) != arm_row.get(field_name)
    )


def decision_delta_count(
    loaded: LoadedAblationRun,
    arm_name: str,
) -> int:
    """Artifact-derived count of questions whose retrieval decision changed.

    Recomputed from the finalized arm rows versus the base arm rows, so the
    number is reproducible without trusting any intermediate file.
    """
    if "base" not in loaded.arms:
        raise ValueError(f"missing_base_arm: family {loaded.run_dir} has no base arm")
    base = _rows_by_question(loaded.arm("base"))
    arm = _rows_by_question(loaded.arms[arm_name])
    common = sorted(set(base) & set(arm))
    return sum(
        1
        for question_id in common
        if _fingerprint(base[question_id]) != _fingerprint(arm[question_id])
    )


def declared_factor(arm: LoadedAblationArm) -> str:
    factors = arm.manifest.changed_factors
    if len(factors) != 1:
        raise ValueError(
            f"factor_not_declared: arm {arm.name} declares changed_factors={factors}, "
            "expected exactly one"
        )
    return factors[0]


def check_factor_isolation(loaded: LoadedAblationRun) -> list[str]:
    """Return factor-isolation issues; every arm may differ only in its factor.

    Manifest level: budgets may differ only for budget arms; reader,
    extractor, embedding, tokenizer, policies, caps, config, and dataset
    hashes are fixed. Row level: payload differences must stay within the
    declared factor's observable fields.
    """
    issues: list[str] = []
    if "base" not in loaded.arms:
        return ["missing_base_arm: family has no base arm"]
    base = loaded.arm("base")
    base_rows = _rows_by_question(base)
    for arm_name, arm in sorted(loaded.arms.items()):
        if arm_name == "base":
            continue
        factor = declared_factor(arm)
        if factor not in KNOWN_FACTORS:
            issues.append(
                f"unknown_factor: arm {arm_name} declares factor {factor!r}; "
                f"known factors are {KNOWN_FACTORS}"
            )
            continue
        budget_changed = (
            arm.manifest.budget.input_tokens != base.manifest.budget.input_tokens
        )
        if budget_changed and factor != "budget":
            issues.append(
                f"factor_leak: non-budget arm {arm_name} changes budget "
                f"(input_tokens {base.manifest.budget.input_tokens} -> "
                f"{arm.manifest.budget.input_tokens})"
            )
        for section, key in (
            ("reader", "model_id"),
            ("extractor", "model_id"),
            ("embedding", "model_id"),
            ("tokenizer", "name"),
            ("tokenizer", "version"),
            ("policies", "extraction"),
            ("policies", "router"),
            ("policies", "retrieval"),
            ("policies", "consolidation"),
            ("budget", "max_items_per_source"),
            ("budget", "max_candidates_per_source"),
        ):
            base_value = getattr(getattr(base.manifest, section, None), key, None)
            arm_value = getattr(getattr(arm.manifest, section, None), key, None)
            if base_value != arm_value:
                issues.append(
                    f"factor_leak: arm {arm_name} changes {section}.{key} "
                    f"({base_value!r} -> {arm_value!r}) outside its factor"
                )
        arm_rows = _rows_by_question(arm)
        for question_id in sorted(base_rows):
            base_row = base_rows[question_id]
            arm_row = arm_rows.get(question_id)
            if arm_row is None:
                issues.append(
                    f"unmatched_question_ids: arm {arm_name} lacks question {question_id}"
                )
                continue
            differing = _differing_fields(base_row, arm_row)
            unexpected = [name for name in differing if name not in FACTOR_FIELDS[factor]]
            if unexpected:
                issues.append(
                    f"factor_leak: arm {arm_name} ({factor}) changes fields "
                    f"{unexpected} outside the declared factor"
                )
    return issues


def analyze_factors(loaded: LoadedAblationRun, *, controlled: bool) -> list[FactorResult]:
    """Emit one structured result per declared factor (artifact-derived only)."""
    isolation_issues = check_factor_isolation(loaded)
    factors: list[str] = []
    for arm in loaded.arms.values():
        if arm.name == "base":
            continue
        factor = declared_factor(arm)
        if factor not in KNOWN_FACTORS:
            continue
        if factor not in factors:
            factors.append(factor)
    results: list[FactorResult] = []
    for factor in KNOWN_FACTORS:
        if factor not in factors:
            continue
        arm_names = tuple(
            sorted(
                arm.name
                for arm in loaded.arms.values()
                if arm.name != "base" and declared_factor(arm) == factor
            )
        )
        deltas = [decision_delta_count(loaded, name) for name in arm_names]
        delta = max(deltas) if deltas else 0
        packing_bound_questions: dict[str, int] = {}
        budget_settings: tuple[int, ...] = ()
        if factor == "budget":
            settings: set[int] = set()
            for name in arm_names:
                rows = list(loaded.arms[name].rows)
                settings.update(int(row.get("budget_tokens", 0)) for row in rows)
                packing_bound_questions[name] = sum(1 for row in rows if row.get("packing_bound"))
            budget_settings = tuple(sorted(settings))
        if controlled:
            status: Literal["active", "no_observed_dataset_effect", "inactive"] = (
                "active" if delta > 0 else "inactive"
            )
        else:
            status = "no_observed_dataset_effect" if delta == 0 else "active"
        results.append(
            FactorResult(
                factor=factor,
                arm_names=arm_names,
                decision_delta_count=delta,
                controlled_active=delta > 0 if controlled else None,
                status=status,
                packing_bound_questions=packing_bound_questions,
                budget_settings=budget_settings,
                issues=tuple(
                    issue for issue in isolation_issues if any(name in issue for name in arm_names)
                ),
            )
        )
    return results


def controlled_activation_issues(controlled: LoadedAblationRun) -> list[str]:
    """Gate D: every required switch must be active on the controlled fixture."""
    results = analyze_factors(controlled, controlled=True)
    factors = {result.factor: result for result in results}
    issues: list[str] = []
    for factor in KNOWN_FACTORS:
        result = factors.get(factor)
        if result is None:
            issues.append(
                f"controlled_switch_missing: factor {factor!r} has no arm on the controlled fixture"
            )
            continue
        if not result.controlled_active:
            issues.append(
                f"controlled_switch_inactive: factor {factor!r} (arm(s) "
                f"{result.arm_names}) produced no decision delta on the "
                "controlled fixture"
            )
    return issues


def budget_binding_issues(loaded: LoadedAblationRun) -> list[str]:
    """Budget experiments need 2+ settings binding publication questions."""
    budget_arms = [
        arm
        for arm in loaded.arms.values()
        if arm.name != "base" and declared_factor(arm) == "budget"
    ]
    if not budget_arms:
        return ["budget_requires_two_settings: no budget arms declared"]
    settings: list[int] = []
    binding: dict[int, int] = {}
    for arm in budget_arms:
        rows = list(arm.rows)
        if not rows:
            continue
        setting = int(rows[0].get("budget_tokens", 0))
        if setting not in settings:
            settings.append(setting)
        binding[setting] = binding.get(setting, 0) + sum(
            1 for row in rows if row.get("packing_bound")
        )
    issues: list[str] = []
    if len(settings) < 2:
        issues.append(
            f"budget_requires_two_settings: only {len(settings)} budget setting(s) "
            f"{settings} declared"
        )
    binding_settings = [setting for setting in settings if binding.get(setting, 0) > 0]
    if len(binding_settings) < 2:
        issues.append(
            f"budget_not_binding: packing binds at only {len(binding_settings)} "
            f"setting(s) {binding_settings} (binding counts: {binding})"
        )
    return issues


@dataclass(frozen=True)
class AblationAnalysis:
    """Complete artifact-derived analysis of one ablation family."""

    dataset: str
    run_id: str
    controlled: bool
    factors: tuple[FactorResult, ...]
    isolation_issues: tuple[str, ...]
    budget_issues: tuple[str, ...]
    controlled_activation_issues: tuple[str, ...]
    valid: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "run_id": self.run_id,
            "controlled": self.controlled,
            "factors": [result.as_dict() for result in self.factors],
            "isolation_issues": list(self.isolation_issues),
            "budget_issues": list(self.budget_issues),
            "controlled_activation_issues": list(self.controlled_activation_issues),
            "valid": self.valid,
        }


def analyze_ablation_run(loaded: LoadedAblationRun, *, controlled: bool) -> AblationAnalysis:
    """Analyze one finalized family; never executes anything."""
    factors = tuple(analyze_factors(loaded, controlled=controlled))
    isolation_issues = tuple(check_factor_isolation(loaded))
    budget_issues = tuple(budget_binding_issues(loaded)) if not controlled else ()
    activation_issues = tuple(controlled_activation_issues(loaded)) if controlled else ()
    valid = not isolation_issues and not budget_issues and not activation_issues
    return AblationAnalysis(
        dataset=loaded.dataset,
        run_id=loaded.manifest.run_id,
        controlled=controlled,
        factors=factors,
        isolation_issues=isolation_issues,
        budget_issues=budget_issues,
        controlled_activation_issues=activation_issues,
        valid=valid,
    )


def analyze_ablation_set(
    controlled: LoadedAblationRun,
    ablations: Sequence[LoadedAblationRun],
) -> dict[str, Any]:
    """Aggregate the controlled analysis and one analysis per dataset family."""
    return {
        "controlled": analyze_ablation_run(controlled, controlled=True).as_dict(),
        "datasets": {
            ablation.dataset: analyze_ablation_run(ablation, controlled=False).as_dict()
            for ablation in ablations
        },
    }
