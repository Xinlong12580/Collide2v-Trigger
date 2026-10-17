"""Calibrate a trigger threshold from event-threshold sidecar files."""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field, replace
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import pyarrow.parquet as pq

from collide2v_trigger.core.configurable_menu import (
    ConfigurableCollide2VTriggerMenu,
)
from collide2v_trigger.core.external import definition_identity

from .threshold_scan import PT_THRESHOLD_COLUMN
from .quality import assess_calibration_quality, quality_criteria


SCORE_FORMAT = "event-threshold-sidecar-v1"


@dataclass(frozen=True)
class ScoreConfiguration:
    """Shared interpretation metadata for a collection of score files."""

    trigger_name: str
    threshold_profile: str
    reference_field: str
    scale_fields: tuple[str, ...]
    base_thresholds_gev: tuple[tuple[str, float], ...]
    lower_bound_gev: float
    upper_bound_gev: float
    score_tolerance_gev: float
    isolation_enabled: bool = True
    target_rate_khz: float | None = field(default=None, compare=False)
    trigger_definition_json: str = field(default="", compare=False)
    trigger_definition_identity: str = ""


@dataclass(frozen=True)
class RateScanPoint:
    """Measured acceptance at one reference-threshold coordinate."""

    threshold_gev: float
    efficiency: float
    rate_khz: float
    passing_events: int
    total_events: int


@dataclass(frozen=True)
class RateCalibrationResult:
    """Saved threshold calibration and the scan points used to obtain it."""

    trigger_name: str
    reference_field: str
    calibrated_threshold_gev: float
    measured_efficiency: float
    measured_rate_khz: float
    passing_events: int
    target_rate_khz: float
    collision_rate_mhz: float
    rate_resolution_khz: float
    method: str
    selection_policy: str
    target_bracketed: bool
    total_events: int
    finite_scores: int
    failed_at_lower_bound: int
    right_censored_at_upper_bound: int
    input_files: tuple[Path, ...]
    output_file: Path
    scan_points_file: Path | None
    scan_points: tuple[RateScanPoint, ...]
    quality: dict[str, Any] = field(default_factory=dict)


class ScoreDistribution:
    """Compact frequency table supporting fast threshold-rate queries."""

    def __init__(self, counts: Counter[float], total_events: int) -> None:
        self.total_events = int(total_events)
        self.scores = sorted(counts)
        self.counts = [int(counts[value]) for value in self.scores]
        self.cumulative_counts: list[int] = []
        running = 0
        for count in self.counts:
            running += count
            self.cumulative_counts.append(running)
        self.finite_scores = running

    def passing_events(self, threshold_gev: float) -> int:
        """Count finite lower-bound scores greater than or equal to a cut."""
        index = bisect_left(self.scores, float(threshold_gev))
        below = self.cumulative_counts[index - 1] if index else 0
        return self.finite_scores - below


def _metadata_text(metadata: dict[bytes, bytes], key: str, path: Path) -> str:
    encoded_key = key.encode("utf-8")
    if encoded_key not in metadata:
        raise ValueError(f"Threshold score file {path} is missing metadata {key!r}")
    return metadata[encoded_key].decode("utf-8")


def _configuration_from_metadata(
    metadata: dict[bytes, bytes],
    path: Path,
) -> ScoreConfiguration:
    prefix = "collide2v_trigger."
    trigger_name = _metadata_text(metadata, prefix + "trigger", path)
    try:
        scale_fields_raw = json.loads(
            _metadata_text(metadata, prefix + "scale_fields", path)
        )
        base_thresholds_raw = json.loads(
            _metadata_text(metadata, prefix + "base_thresholds_gev", path)
        )
        lower = float(_metadata_text(metadata, prefix + "lower_bound_gev", path))
        upper = float(_metadata_text(metadata, prefix + "upper_bound_gev", path))
        tolerance = float(_metadata_text(metadata, prefix + "tolerance_gev", path))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid threshold metadata in {path}: {exc}") from exc
    if not isinstance(scale_fields_raw, list) or not all(
        isinstance(value, str) for value in scale_fields_raw
    ):
        raise ValueError(f"Invalid scale_fields metadata in {path}")
    if not isinstance(base_thresholds_raw, dict):
        raise ValueError(f"Invalid base_thresholds_gev metadata in {path}")
    try:
        base_thresholds = tuple(
            sorted((str(key), float(value)) for key, value in base_thresholds_raw.items())
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid base thresholds in {path}") from exc
    target_raw = metadata.get(b"collide2v_trigger.target_rate_khz")
    isolation_enabled = json.loads(metadata.get(b"collide2v_trigger.isolation_enabled", b"true"))
    if not isinstance(isolation_enabled, bool):
        raise ValueError(f"Invalid isolation_enabled metadata in {path}")
    target = None if target_raw is None else float(target_raw)
    if target is not None and (not math.isfinite(target) or target < 0):
        raise ValueError(f"Invalid target_rate_khz metadata in {path}")
    provenance_raw = metadata.get(b"collide2v_trigger.trigger_definition", b"").decode()
    identity = ""
    if provenance_raw:
        provenance = json.loads(provenance_raw)
        if not isinstance(provenance, dict) or not isinstance(provenance.get("definition"), dict):
            raise ValueError(f"Invalid external trigger definition metadata in {path}")
        definition = provenance["definition"]
        if definition.get("name") != trigger_name or definition.get("target_rate_khz") != target:
            raise ValueError(f"External trigger name or target disagrees with score metadata in {path}")
        identity = json.dumps(definition_identity(provenance), sort_keys=True)
    return ScoreConfiguration(
        trigger_name=trigger_name,
        threshold_profile=_metadata_text(
            metadata, prefix + "threshold_profile", path
        ),
        reference_field=_metadata_text(metadata, prefix + "reference_field", path),
        scale_fields=tuple(scale_fields_raw),
        base_thresholds_gev=base_thresholds,
        lower_bound_gev=lower,
        upper_bound_gev=upper,
        score_tolerance_gev=tolerance,
        isolation_enabled=isolation_enabled,
        target_rate_khz=target,
        trigger_definition_json=provenance_raw,
        trigger_definition_identity=identity,
    )


def discover_threshold_score_files(
    input_directory: str | Path,
    *,
    trigger_name: str | None = None,
) -> tuple[list[Path], ScoreConfiguration]:
    """Find compatible score sidecars and infer their trigger from metadata."""
    directory = Path(input_directory).expanduser()
    if not directory.is_dir():
        raise NotADirectoryError(f"Threshold-score directory does not exist: {directory}")

    by_trigger: dict[str, list[tuple[Path, ScoreConfiguration]]] = {}
    for path in sorted(directory.glob("*.parquet")):
        schema = pq.read_schema(path)
        metadata = dict(schema.metadata or {})
        if metadata.get(b"collide2v_trigger.format", b"").decode("utf-8") != SCORE_FORMAT:
            continue
        if schema.names != [PT_THRESHOLD_COLUMN]:
            raise ValueError(
                f"Threshold score file {path} must contain only "
                f"{PT_THRESHOLD_COLUMN!r}; found {schema.names}"
            )
        configuration = _configuration_from_metadata(metadata, path)
        by_trigger.setdefault(configuration.trigger_name, []).append(
            (path, configuration)
        )

    if not by_trigger:
        raise FileNotFoundError(
            f"No {SCORE_FORMAT} Parquet files found directly under {directory}"
        )
    if trigger_name is None:
        if len(by_trigger) != 1:
            found = ", ".join(sorted(by_trigger))
            raise ValueError(
                "Multiple triggers were found in the score directory; select one "
                f"explicitly. Found: {found}"
            )
        trigger_name = next(iter(by_trigger))
    if trigger_name not in by_trigger:
        found = ", ".join(sorted(by_trigger))
        raise ValueError(
            f"No score files for trigger {trigger_name!r}; available triggers: {found}"
        )

    selected = by_trigger[trigger_name]
    expected = selected[0][1]
    targets = {config.target_rate_khz for _, config in selected if config.target_rate_khz is not None}
    if len(targets) > 1:
        raise ValueError("Selected score files use inconsistent target rates")
    if targets:
        expected = replace(expected, target_rate_khz=next(iter(targets)))
    incompatible = [path for path, config in selected if config != expected]
    if incompatible:
        names = ", ".join(str(path) for path in incompatible[:5])
        raise ValueError(
            "Selected score files use inconsistent scan configurations: " + names
        )

    sources: dict[str, Path] = {}
    for path, _ in selected:
        metadata = dict(pq.read_schema(path).metadata or {})
        source = _metadata_text(metadata, "collide2v_trigger.source", path)
        if source in sources:
            raise ValueError(
                f"Duplicate event source in score files {sources[source]} and {path}: "
                f"{source}"
            )
        sources[source] = path
    return [path for path, _ in selected], expected


def load_score_distribution(
    score_files: Iterable[Path],
    configuration: ScoreConfiguration,
) -> tuple[ScoreDistribution, int]:
    """Aggregate scores once, including failed events in the denominator."""
    counts: Counter[float] = Counter()
    total_events = 0
    failed_at_lower = 0
    for path in score_files:
        parquet_file = pq.ParquetFile(path)
        total_events += parquet_file.metadata.num_rows
        values = parquet_file.read(columns=[PT_THRESHOLD_COLUMN]).column(0).to_pylist()
        for value in values:
            if value is None or math.isnan(float(value)):
                failed_at_lower += 1
                continue
            score = float(value)
            if not math.isfinite(score):
                raise ValueError(f"Non-finite threshold score in {path}: {score}")
            if not (
                configuration.lower_bound_gev
                <= score
                <= configuration.upper_bound_gev
            ):
                raise ValueError(
                    f"Threshold score {score} in {path} is outside the stored scan range"
                )
            counts[score] += 1
    if total_events == 0:
        raise ValueError("Threshold score files contain no events")
    if total_events != failed_at_lower + sum(counts.values()):
        raise RuntimeError("Threshold score accounting is inconsistent")
    return ScoreDistribution(counts, total_events), failed_at_lower


def _measure(
    distribution: ScoreDistribution,
    threshold_gev: float,
    collision_rate_khz: float,
) -> RateScanPoint:
    passing = distribution.passing_events(threshold_gev)
    efficiency = passing / distribution.total_events
    return RateScanPoint(
        threshold_gev=float(threshold_gev),
        efficiency=efficiency,
        rate_khz=collision_rate_khz * efficiency,
        passing_events=passing,
        total_events=distribution.total_events,
    )


def _choose_point(
    points: Sequence[RateScanPoint],
    target_rate_khz: float,
    selection_policy: str,
) -> RateScanPoint:
    if selection_policy == "at_most":
        safe = [point for point in points if point.rate_khz <= target_rate_khz]
        return min(safe, key=lambda point: point.threshold_gev) if safe else points[-1]
    return min(
        points,
        key=lambda point: (
            abs(point.rate_khz - target_rate_khz),
            point.rate_khz > target_rate_khz,
            point.threshold_gev,
        ),
    )


def _monotonic_scan(
    distribution: ScoreDistribution,
    *,
    lower_bound_gev: float,
    upper_bound_gev: float,
    spacing_gev: float,
    target_rate_khz: float,
    collision_rate_khz: float,
    selection_policy: str,
) -> tuple[RateScanPoint, list[RateScanPoint]]:
    if not math.isfinite(spacing_gev) or spacing_gev <= 0.0:
        raise ValueError("spacing_gev must be finite and positive")
    step_count = math.floor((upper_bound_gev - lower_bound_gev) / spacing_gev)
    thresholds = [
        lower_bound_gev + index * spacing_gev for index in range(step_count + 1)
    ]
    if thresholds[-1] < upper_bound_gev:
        thresholds.append(upper_bound_gev)
    points = [
        _measure(distribution, threshold, collision_rate_khz)
        for threshold in thresholds
    ]
    return _choose_point(points, target_rate_khz, selection_policy), points


def _bisection_scan(
    distribution: ScoreDistribution,
    *,
    lower_bound_gev: float,
    upper_bound_gev: float,
    tolerance_gev: float,
    target_rate_khz: float,
    collision_rate_khz: float,
    selection_policy: str,
) -> tuple[RateScanPoint, list[RateScanPoint]]:
    if not math.isfinite(tolerance_gev) or tolerance_gev <= 0.0:
        raise ValueError("tolerance_gev must be finite and positive")
    evaluated: dict[float, RateScanPoint] = {}

    def evaluate(threshold: float) -> RateScanPoint:
        if threshold not in evaluated:
            evaluated[threshold] = _measure(
                distribution, threshold, collision_rate_khz
            )
        return evaluated[threshold]

    low = lower_bound_gev
    high = upper_bound_gev
    low_point = evaluate(low)
    high_point = evaluate(high)
    target_bracketed = low_point.rate_khz >= target_rate_khz >= high_point.rate_khz

    if target_bracketed and low_point.rate_khz > target_rate_khz:
        while high - low > tolerance_gev:
            midpoint = (low + high) / 2.0
            point = evaluate(midpoint)
            if point.rate_khz > target_rate_khz:
                low = midpoint
                low_point = point
            else:
                high = midpoint
                high_point = point

    bracket_points = [low_point, high_point]
    chosen = _choose_point(bracket_points, target_rate_khz, selection_policy)
    return chosen, sorted(evaluated.values(), key=lambda point: point.threshold_gev)


def _write_scan_points(
    path: Path,
    *,
    metadata: dict[str, object],
    points: Sequence[RateScanPoint],
) -> None:
    """Write a self-describing, human-readable threshold scan table."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def encode(value: object) -> str:
        if isinstance(value, float):
            return f"{value:.12g}"
        if isinstance(value, (dict, list, bool)) or value is None:
            return json.dumps(value, separators=(",", ":"), sort_keys=True)
        return str(value)

    lines = [f"# {key}: {encode(value)}" for key, value in metadata.items()]
    lines.append(
        "threshold_gev\tefficiency\trate_khz\tpassing_events\ttotal_events"
    )
    lines.extend(
        f"{point.threshold_gev:.12g}\t{point.efficiency:.12g}\t"
        f"{point.rate_khz:.12g}\t{point.passing_events}\t{point.total_events}"
        for point in points
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def calibrate_rate_from_threshold_scores(
    input_directory: str | Path,
    *,
    trigger_name: str | None = None,
    target_rate_khz: float | None = None,
    method: str = "bisection",
    spacing_gev: float = 1.0,
    tolerance_gev: float | None = None,
    lower_bound_gev: float | None = None,
    upper_bound_gev: float | None = None,
    collision_rate_mhz: float = 40.0,
    selection_policy: str = "at_most",
    rate_tolerance_relative: float = 0.10,
    rate_tolerance_khz: float | None = None,
    min_passing_events: int = 25,
    output_file: str | Path | None = None,
    scan_points_file: str | Path | None = None,
) -> RateCalibrationResult:
    """Find and save a threshold that approaches a requested trigger rate.

    ``method='monotonic'`` evaluates a regular threshold grid with
    ``spacing_gev``. ``method='bisection'`` searches until ``tolerance_gev``;
    when omitted, the score files' own bisection tolerance is used. The
    default ``at_most`` policy chooses the lowest evaluated threshold whose
    rate does not exceed the target.
    Quality reporting flags rate mismatches and statistically weak or boundary
    solutions independently of the threshold-space stopping criterion.
    """
    quality_criteria(
        rate_tolerance_relative=rate_tolerance_relative,
        rate_tolerance_khz=rate_tolerance_khz,
        min_passing_events=min_passing_events,
    )
    if method not in {"monotonic", "bisection"}:
        raise ValueError("method must be 'monotonic' or 'bisection'")
    if selection_policy not in {"at_most", "closest"}:
        raise ValueError("selection_policy must be 'at_most' or 'closest'")
    if not math.isfinite(collision_rate_mhz) or collision_rate_mhz <= 0.0:
        raise ValueError("collision_rate_mhz must be finite and positive")

    score_files, configuration = discover_threshold_score_files(
        input_directory,
        trigger_name=trigger_name,
    )
    trigger_name = configuration.trigger_name
    if target_rate_khz is None:
        target_rate_khz = configuration.target_rate_khz
        if target_rate_khz is None:
            target_rate_khz = ConfigurableCollide2VTriggerMenu().target_rate_khz(trigger_name)
    target_rate_khz = float(target_rate_khz)
    collision_rate_khz = collision_rate_mhz * 1000.0
    if not math.isfinite(target_rate_khz) or not (
        0.0 <= target_rate_khz <= collision_rate_khz
    ):
        raise ValueError(
            f"target_rate_khz must be between 0 and {collision_rate_khz:g}"
        )

    lower = (
        configuration.lower_bound_gev
        if lower_bound_gev is None
        else float(lower_bound_gev)
    )
    upper = (
        configuration.upper_bound_gev
        if upper_bound_gev is None
        else float(upper_bound_gev)
    )
    if not (
        configuration.lower_bound_gev <= lower < upper <= configuration.upper_bound_gev
    ):
        raise ValueError(
            "Calibration bounds must lie within the score-file range "
            f"[{configuration.lower_bound_gev:g}, {configuration.upper_bound_gev:g}]"
        )

    distribution, failed_at_lower = load_score_distribution(
        score_files,
        configuration,
    )
    rate_at_lower = _measure(distribution, lower, collision_rate_khz).rate_khz
    rate_at_upper = _measure(distribution, upper, collision_rate_khz).rate_khz
    target_bracketed = rate_at_lower >= target_rate_khz >= rate_at_upper

    if method == "monotonic":
        chosen, points = _monotonic_scan(
            distribution,
            lower_bound_gev=lower,
            upper_bound_gev=upper,
            spacing_gev=spacing_gev,
            target_rate_khz=target_rate_khz,
            collision_rate_khz=collision_rate_khz,
            selection_policy=selection_policy,
        )
    else:
        tolerance = (
            configuration.score_tolerance_gev
            if tolerance_gev is None
            else float(tolerance_gev)
        )
        if tolerance < configuration.score_tolerance_gev:
            raise ValueError(
                "tolerance_gev cannot be smaller than the event-score precision "
                f"({configuration.score_tolerance_gev:g} GeV)"
            )
        chosen, points = _bisection_scan(
            distribution,
            lower_bound_gev=lower,
            upper_bound_gev=upper,
            tolerance_gev=tolerance,
            target_rate_khz=target_rate_khz,
            collision_rate_khz=collision_rate_khz,
            selection_policy=selection_policy,
        )

    input_root = Path(input_directory).expanduser()
    saved_output = (
        input_root / f"{trigger_name}_{method}_calibrated_threshold.json"
        if output_file is None
        else Path(output_file).expanduser()
    )
    if saved_output.suffix.lower() != ".json":
        raise ValueError("output_file must have a .json extension")
    saved_points = (
        None if scan_points_file is None else Path(scan_points_file).expanduser()
    )
    if saved_points is not None and saved_points.suffix.lower() != ".txt":
        raise ValueError("scan_points_file must have a .txt extension")

    right_censored = distribution.counts[-1] if (
        distribution.scores
        and distribution.scores[-1] == configuration.upper_bound_gev
    ) else 0
    result = RateCalibrationResult(
        trigger_name=trigger_name,
        reference_field=configuration.reference_field,
        calibrated_threshold_gev=chosen.threshold_gev,
        measured_efficiency=chosen.efficiency,
        measured_rate_khz=chosen.rate_khz,
        passing_events=chosen.passing_events,
        target_rate_khz=target_rate_khz,
        collision_rate_mhz=collision_rate_mhz,
        rate_resolution_khz=collision_rate_khz / distribution.total_events,
        method=method,
        selection_policy=selection_policy,
        target_bracketed=target_bracketed,
        total_events=distribution.total_events,
        finite_scores=distribution.finite_scores,
        failed_at_lower_bound=failed_at_lower,
        right_censored_at_upper_bound=right_censored,
        input_files=tuple(score_files),
        output_file=saved_output,
        scan_points_file=saved_points,
        scan_points=tuple(points),
    )

    summary = {
        "isolation_enabled": configuration.isolation_enabled,
        "schema_version": 1,
        "trigger_name": result.trigger_name,
        "threshold_profile": configuration.threshold_profile,
        "reference_field": result.reference_field,
        "scale_fields": list(configuration.scale_fields),
        "base_thresholds_gev": dict(configuration.base_thresholds_gev),
        "calibrated_threshold_gev": result.calibrated_threshold_gev,
        "measured_efficiency": result.measured_efficiency,
        "measured_rate_khz": result.measured_rate_khz,
        "passing_events": result.passing_events,
        "target_rate_khz": result.target_rate_khz,
        "collision_rate_mhz": result.collision_rate_mhz,
        "rate_resolution_khz": result.rate_resolution_khz,
        "efficiency_weighting": "unweighted events",
        "method": result.method,
        "selection_policy": result.selection_policy,
        "target_bracketed": result.target_bracketed,
        "rate_at_lower_bound_khz": rate_at_lower,
        "rate_at_upper_bound_khz": rate_at_upper,
        "calibration_lower_bound_gev": lower,
        "calibration_upper_bound_gev": upper,
        "spacing_gev": spacing_gev if method == "monotonic" else None,
        "bisection_tolerance_gev": (
            configuration.score_tolerance_gev
            if method == "bisection" and tolerance_gev is None
            else tolerance_gev if method == "bisection" else None
        ),
        "score_tolerance_gev": configuration.score_tolerance_gev,
        "score_semantics": (
            "pt_threshold_gev is the largest threshold confirmed to pass; "
            "the true boundary is less than score_tolerance_gev above it"
        ),
        "total_events": result.total_events,
        "finite_scores": result.finite_scores,
        "failed_at_lower_bound": result.failed_at_lower_bound,
        "right_censored_at_upper_bound": result.right_censored_at_upper_bound,
        "input_score_files": [str(path) for path in result.input_files],
        "scan_points_file": str(saved_points) if saved_points is not None else None,
    }
    if configuration.trigger_definition_json:
        summary["trigger_definition"] = json.loads(configuration.trigger_definition_json)
    summary["default_target_rate_khz"] = configuration.target_rate_khz
    quality = assess_calibration_quality(
        summary,
        rate_tolerance_relative=rate_tolerance_relative,
        rate_tolerance_khz=rate_tolerance_khz,
        min_passing_events=min_passing_events,
    )
    result = replace(result, quality=quality)
    summary["quality"] = quality
    saved_output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = saved_output.with_suffix(saved_output.suffix + ".tmp")
    temporary_output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_output.replace(saved_output)
    if saved_points is not None:
        points_metadata = {
            "isolation_enabled": configuration.isolation_enabled,
            "format": "rate-scan-points-v2",
            "trigger": result.trigger_name,
            "method": result.method,
            "threshold_profile": configuration.threshold_profile,
            "reference_field": result.reference_field,
            "scale_fields": list(configuration.scale_fields),
            "base_thresholds_gev": dict(configuration.base_thresholds_gev),
            "target_rate_khz": result.target_rate_khz,
            "collision_rate_mhz": result.collision_rate_mhz,
            "calibrated_threshold_gev": result.calibrated_threshold_gev,
            "measured_efficiency": result.measured_efficiency,
            "measured_rate_khz": result.measured_rate_khz,
            "passing_events": result.passing_events,
            "total_events": result.total_events,
            "rate_resolution_khz": result.rate_resolution_khz,
            "selection_policy": result.selection_policy,
            "target_bracketed": result.target_bracketed,
            "calibration_lower_bound_gev": lower,
            "calibration_upper_bound_gev": upper,
            "spacing_gev": spacing_gev if method == "monotonic" else None,
            "bisection_tolerance_gev": (
                configuration.score_tolerance_gev
                if method == "bisection" and tolerance_gev is None
                else tolerance_gev if method == "bisection" else None
            ),
            "score_tolerance_gev": configuration.score_tolerance_gev,
            "efficiency_weighting": "unweighted events",
            "quality": quality,
        }
        if configuration.trigger_definition_json:
            points_metadata["trigger_definition"] = json.loads(configuration.trigger_definition_json)
        _write_scan_points(
            saved_points,
            metadata=points_metadata,
            points=points,
        )

    print(f"Trigger: {trigger_name}")
    print(
        f"Target rate: {target_rate_khz:g} kHz; calibrated "
        f"{configuration.reference_field}: {chosen.threshold_gev:g} GeV"
    )
    print(
        f"Measured efficiency: {chosen.efficiency:.8g}; "
        f"measured rate: {chosen.rate_khz:.8g} kHz "
        f"({chosen.passing_events}/{chosen.total_events} events)"
    )
    if not target_bracketed:
        print("Target rate is outside the rate range covered by the chosen bounds.")
    print(f"Quality: {quality['status']}")
    for flag, message in zip(quality["flags"], quality["messages"]):
        print(f"  [{flag}] {message}")
    print(f"Saved calibrated threshold: {saved_output}")
    if saved_points is not None:
        print(f"Saved {len(points)} scan points: {saved_points}")
    return result


__all__ = [
    "RateCalibrationResult",
    "RateScanPoint",
    "ScoreConfiguration",
    "calibrate_rate_from_threshold_scores",
    "discover_threshold_score_files",
    "load_score_distribution",
]
