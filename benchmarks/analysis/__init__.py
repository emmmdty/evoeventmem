"""M15 analysis tooling (Workstream C).

Public surface: artifact loading, content-addressed finalization, paired
statistics, ablation analysis, failure taxonomy, and report generation.
"""

from benchmarks.analysis.bootstrap import (
    BootstrapCI,
    ComparisonResult,
    holm_adjust,
    paired_bootstrap_ci,
)
from benchmarks.analysis.finalization import (
    AnalysisConfig,
    AnalysisFinalization,
    AnalysisInputError,
    derive_analysis_id,
    ensure_analysis_artifact,
    load_config,
)
from benchmarks.analysis.loaders import (
    LoadedAblationRun,
    LoadedRun,
    load_ablation_run,
    load_base_run,
)
from benchmarks.analysis.report import Claim, generate_report
from benchmarks.analysis.taxonomy import FailureType
from benchmarks.analysis.validate_report import (
    ValidationResult,
    validate_analysis_inputs,
)

__all__ = [
    "AnalysisConfig",
    "AnalysisFinalization",
    "AnalysisInputError",
    "BootstrapCI",
    "Claim",
    "ComparisonResult",
    "FailureType",
    "LoadedAblationRun",
    "LoadedRun",
    "ValidationResult",
    "derive_analysis_id",
    "ensure_analysis_artifact",
    "generate_report",
    "holm_adjust",
    "load_ablation_run",
    "load_base_run",
    "load_config",
    "paired_bootstrap_ci",
    "validate_analysis_inputs",
]
