from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.mechanism.consistency import (
    budget_saturation,
    compute_run_consistency,
    etec_actions,
    provenance_coverage,
    review_calibration,
    summarize_locomo,
    wilson_ci,
)

R2_RUN = Path("runs/publication/longmemeval-test20-r2")
R2_REVIEW = Path("runs/review/longmemeval-r2.reviewed.jsonl")
LOCOMO_REPORT = Path("runs/main/report")


def test_wilson_ci_unanimous_upper_is_one() -> None:
    lo, hi = wilson_ci(100, 100)
    assert hi == 1.0
    assert 0.0 < lo < 1.0


def test_wilson_ci_zero_denominator_is_degenerate() -> None:
    assert wilson_ci(0, 0) == (0.0, 0.0)


def test_wilson_ci_partial_is_bounded() -> None:
    lo, hi = wilson_ci(5, 10)
    assert 0.0 <= lo < 0.5 < hi <= 1.0


def test_provenance_coverage_all_carry_raw_turn_id() -> None:
    rows = [
        {
            "method": "etec",
            "packing_bound": True,
            "packed_items": [
                {"evidence_refs": [{"raw_turn_id": "s1:0"}, {"raw_turn_id": "s1:1"}]},
                {"evidence_refs": [{"raw_turn_id": "s2:0"}]},
            ],
        }
    ]
    stats = provenance_coverage(rows)
    assert stats["numerator"] == 3
    assert stats["denominator"] == 3
    assert stats["point_estimate"] == 1.0
    assert stats["wilson_95ci_high"] == 1.0


def test_provenance_coverage_counts_missing_raw_turn_id() -> None:
    rows = [
        {
            "method": "full",
            "packing_bound": True,
            "packed_items": [
                {"evidence_refs": [{"raw_turn_id": "s1:0"}, {"session_id": "s1"}]},
            ],
        }
    ]
    stats = provenance_coverage(rows)
    assert stats["numerator"] == 1
    assert stats["denominator"] == 2
    assert 0.0 < stats["point_estimate"] < 1.0


def test_budget_saturation_memory_methods_only() -> None:
    rows = [
        {"method": "etec", "packing_bound": True, "budget_tokens": 4096},
        {"method": "full", "packing_bound": True, "budget_tokens": 4096},
        {"method": "vector_rag", "packing_bound": False, "budget_tokens": 4096},
    ]
    stats = budget_saturation(rows)
    assert set(stats["memory_methods"]) == {"etec", "full", "vector_rag"}
    assert stats["memory_methods"]["etec"]["point_estimate"] == 1.0
    assert stats["memory_methods"]["vector_rag"]["point_estimate"] == 0.0
    assert stats["memory_methods_aggregate"]["numerator"] == 2
    assert stats["memory_methods_aggregate"]["denominator"] == 3
    assert stats["budget_tokens_limit"] == 4096


def test_etec_actions_na_when_no_samples_dir(tmp_path: Path) -> None:
    result = etec_actions(tmp_path)
    assert result["status"] == "na_no_samples_dir"
    assert result["actions"] is None


def test_etec_actions_aggregates_from_samples(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "q1.json").write_text(
        json.dumps(
            {"sample_id": "q1", "ingestion": {"etec": {"actions": {"ADD": 10, "MERGE": 2}}}}
        ),
        encoding="utf-8",
    )
    (samples / "q2.json").write_text(
        json.dumps(
            {"sample_id": "q2", "ingestion": {"etec": {"actions": {"ADD": 5, "SUPERSEDE": 1}}}}
        ),
        encoding="utf-8",
    )
    (samples / "q1.extraction_snapshot.json").write_text("{}", encoding="utf-8")
    result = etec_actions(tmp_path)
    assert result["status"] == "ok"
    assert result["actions"] == {"ADD": 15, "MERGE": 2, "SUPERSEDE": 1, "REJECT": 0}
    assert result["sample_count"] == 2


def test_review_calibration_agreement(tmp_path: Path) -> None:
    sheet = tmp_path / "review.jsonl"
    rows = [
        {
            "automatic_label": "budget_truncation",
            "reviewer_label": "answer_present_reader_wrong",
        },
        {
            "automatic_label": "budget_truncation",
            "reviewer_label": "answer_present_reader_wrong",
        },
        {
            "automatic_label": "extraction_provenance_rejection",
            "reviewer_label": "extraction_provenance_rejection",
        },
    ]
    sheet.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    cal = review_calibration(sheet)
    assert cal["total_reviewed"] == 3
    assert cal["agreement"]["numerator"] == 1
    assert cal["agreement"]["point_estimate"] == pytest.approx(1 / 3)


def test_summarize_locomo_reads_csv_and_error_review(tmp_path: Path) -> None:
    report = tmp_path / "report"
    (report / "tables").mkdir(parents=True)
    (report / "tables" / "overall.csv").write_text(
        "method,questions,exact_match,token_f1,evidence_f1,tokens_per_query\n"
        "vector_rag,1986,0.0861,0.1873,0.0020,142.2\n"
        "full_context,1986,0.0670,0.1507,0.0020,4102.3\n",
        encoding="utf-8",
    )
    (report / "error_review.jsonl").write_text(
        json.dumps({"failure_category": "answer_not_recoverable_from_context"}) + "\n"
        + json.dumps({"failure_category": "adversarial_no_gold_answer"}) + "\n",
        encoding="utf-8",
    )
    summary = summarize_locomo(report)
    assert summary["question_count"] == 1986
    assert summary["tokens_per_query"]["vector_rag"] == 142.2
    assert summary["tokens_per_query"]["full_context"] == 4102.3
    assert summary["failure_distribution"]["distribution"] == {
        "answer_not_recoverable_from_context": 1,
        "adversarial_no_gold_answer": 1,
    }
    assert summary["provenance_coverage"]["point_estimate"] == 0.0
    assert summary["provenance_coverage"]["status"] == "legacy_defect"


@pytest.mark.skipif(
    not (R2_RUN / "finalized" / "FINALIZED.json").exists(),
    reason="longmemeval-test20-r2 run artifacts not present",
)
def test_compute_run_consistency_recomputes_r2_numbers() -> None:
    """End-to-end recomputation over the finalized r2 run (gated on runs/)."""
    consistency = compute_run_consistency(R2_RUN)
    prov = consistency["provenance_coverage"]
    assert prov["numerator"] == 4701
    assert prov["denominator"] == 4701
    assert prov["point_estimate"] == 1.0
    sat = consistency["budget_saturation"]["memory_methods_aggregate"]
    assert sat["numerator"] == 72
    assert sat["denominator"] == 72
    assert sat["point_estimate"] == 1.0
    zc = consistency["zero_score_cells"]
    assert zc["total_zero_cells"] == 4
    assert zc["memory_method_zero_cells"] == 4
    assert zc["baseline_method_zero_cells"] == 0
    actions = consistency["etec_actions"]["actions"]
    assert actions["ADD"] == 5429
    assert actions["MERGE"] == 5
    assert actions["SUPERSEDE"] == 0
    assert actions["REJECT"] == 0


@pytest.mark.skipif(
    not (R2_REVIEW).exists(),
    reason="r2 review sheet not present",
)
def test_review_calibration_matches_real_r2_sheet() -> None:
    cal = review_calibration(R2_REVIEW)
    assert cal["total_reviewed"] == 33
    assert cal["agreement"]["numerator"] == 7
    assert cal["reviewer_label_distribution"]["answer_present_reader_wrong"] == 26
