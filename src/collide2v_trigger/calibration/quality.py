"""Reporting checks for rate fits; these do not change the threshold search."""

from __future__ import annotations

import math
from typing import Any, Mapping


FLAG_MESSAGES = {
    "zero_target": "Zero target: zero observed acceptance does not establish a zero population rate.",
    "threshold_undetermined": "No events pass at the lower bound; the zero-rate target does not determine a threshold.",
    "no_events_at_lower_bound": "No events pass at the lower scan bound.",
    "target_not_bracketed": "The target lies outside the rate range covered by the scan bounds.",
    "zero_acceptance": "The selected threshold accepts zero events despite a positive target.",
    "rate_mismatch": "The selected rate differs from the target by more than the reporting tolerance.",
    "low_statistics": "Too few events pass for the configured statistical quality criterion.",
    "near_lower_bound": "The selected threshold is within one search step or score tolerance of the lower bound.",
    "near_upper_bound": "The selected threshold is within one search step or score tolerance of the upper bound.",
    "right_censored_scores": "Some events still pass at the event-score upper bound.",
}


def quality_criteria(
    *,
    rate_tolerance_relative: float = 0.10,
    rate_tolerance_khz: float | None = None,
    min_passing_events: int = 25,
) -> dict[str, Any]:
    """Validate reporting options independently of any data or EOS access."""
    if not math.isfinite(rate_tolerance_relative) or rate_tolerance_relative < 0:
        raise ValueError("rate_tolerance_relative must be finite and nonnegative")
    if rate_tolerance_khz is not None and (
        not math.isfinite(rate_tolerance_khz) or rate_tolerance_khz < 0
    ):
        raise ValueError("rate_tolerance_khz must be finite and nonnegative")
    if isinstance(min_passing_events, bool) or not isinstance(min_passing_events, int) or min_passing_events < 0:
        raise ValueError("min_passing_events must be a nonnegative integer")
    return {
        "rate_tolerance_relative": rate_tolerance_relative,
        "rate_tolerance_khz": rate_tolerance_khz,
        "min_passing_events": min_passing_events,
        "effective_rate_tolerance_policy": (
            "max(relative tolerance * target, absolute tolerance); "
            "absolute tolerance defaults to one event; zero targets require zero observed passes"
        ),
    }


def assess_calibration_quality(
    summary: Mapping[str, Any],
    *,
    rate_tolerance_relative: float = 0.10,
    rate_tolerance_khz: float | None = None,
    min_passing_events: int = 25,
) -> dict[str, Any]:
    """Annotate a calibration JSON summary using configurable reporting criteria.

    Threshold convergence, empirical rate agreement, and event statistics are
    separate concepts. An ``ok`` result passes these checks on this sample;
    it is not a claim of independent validation or representative pileup.
    """
    criteria = quality_criteria(
        rate_tolerance_relative=rate_tolerance_relative,
        rate_tolerance_khz=rate_tolerance_khz,
        min_passing_events=min_passing_events,
    )
    target = float(summary["target_rate_khz"])
    measured = float(summary["measured_rate_khz"])
    passed = int(summary["passing_events"])
    total = int(summary["total_events"])
    collision_rate_khz = float(summary["collision_rate_mhz"]) * 1000.0
    resolution = collision_rate_khz / total
    difference = measured - target
    absolute_tolerance = resolution if rate_tolerance_khz is None else rate_tolerance_khz
    effective_tolerance = (
        max(absolute_tolerance, target * rate_tolerance_relative) if target > 0 else 0.0
    )
    within = abs(difference) <= effective_tolerance or math.isclose(
        abs(difference), effective_tolerance, rel_tol=1e-12, abs_tol=1e-12
    )
    if target == 0:
        within = passed == 0
    rate_at_lower = float(summary.get(
        "rate_at_lower_bound_khz", int(summary["finite_scores"]) * resolution
    ))
    threshold = float(summary["calibrated_threshold_gev"])
    lower = float(summary["calibration_lower_bound_gev"])
    upper = float(summary["calibration_upper_bound_gev"])
    boundary_precision = max(
        float(summary["score_tolerance_gev"]),
        float(summary.get("bisection_tolerance_gev") or summary.get("spacing_gev") or 0),
    )

    flags = []
    if target == 0:
        flags.append("zero_target")
        if rate_at_lower == 0:
            flags.append("threshold_undetermined")
    if rate_at_lower == 0:
        flags.append("no_events_at_lower_bound")
    if not summary["target_bracketed"]:
        flags.append("target_not_bracketed")
    if target > 0 and passed == 0:
        flags.append("zero_acceptance")
    if not within:
        flags.append("rate_mismatch")
    if target > 0 and passed < min_passing_events:
        flags.append("low_statistics")
    if threshold - lower <= boundary_precision + 1e-12:
        flags.append("near_lower_bound")
    if upper - threshold <= boundary_precision + 1e-12:
        flags.append("near_upper_bound")
    if summary.get("right_censored_at_upper_bound", 0) > 0:
        flags.append("right_censored_scores")

    return {
        "status": "needs_review" if flags else "ok",
        "flags": flags,
        "messages": [FLAG_MESSAGES[flag] for flag in flags],
        "within_rate_tolerance": within,
        "rate_difference_khz": difference,
        "relative_rate_difference": difference / target if target > 0 else None,
        "effective_rate_tolerance_khz": effective_tolerance,
        "naive_relative_statistical_uncertainty": 1.0 / math.sqrt(passed) if passed else None,
        "statistical_uncertainty_assumption": "Independent events; ignores repeated pileup and file correlations.",
        "criteria": criteria,
        "scope": "Reporting only; threshold search and export policy are unchanged.",
    }


__all__ = ["assess_calibration_quality", "quality_criteria"]
