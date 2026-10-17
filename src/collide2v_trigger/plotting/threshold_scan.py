"""Read and plot a threshold-versus-rate scan produced by the calibrator."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
import textwrap
from pathlib import Path
from statistics import median
from typing import Any, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


REQUIRED_COLUMNS = (
    "threshold_gev",
    "efficiency",
    "rate_khz",
    "passing_events",
    "total_events",
)


@dataclass(frozen=True)
class ThresholdRateScan:
    """Parsed rate scan plus the physics metadata needed to interpret it."""

    source_file: Path
    calibration_file: Path | None
    trigger_name: str
    method: str
    reference_field: str
    scale_fields: tuple[str, ...]
    base_thresholds_gev: Mapping[str, float]
    target_rate_khz: float
    collision_rate_mhz: float
    selected_threshold_gev: float
    selected_efficiency: float
    selected_rate_khz: float
    selected_passing_events: int
    total_events: int
    rate_resolution_khz: float
    selection_policy: str
    threshold_profile: str
    thresholds_gev: tuple[float, ...]
    efficiencies: tuple[float, ...]
    rates_khz: tuple[float, ...]
    passing_events: tuple[int, ...]
    metadata: Mapping[str, Any]


def _decode_metadata(value: str) -> Any:
    """Decode JSON-like values while accepting the older unquoted headers."""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _read_table(path: Path) -> tuple[dict[str, Any], dict[str, list[float | int]]]:
    metadata: dict[str, Any] = {}
    header: list[str] | None = None
    columns: dict[str, list[float | int]] = {}

    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            entry = stripped[1:].strip()
            if ":" not in entry:
                continue
            key, raw_value = entry.split(":", 1)
            key = key.strip()
            if key in metadata:
                raise ValueError(f"Duplicate metadata key {key!r} at line {line_number}")
            metadata[key] = _decode_metadata(raw_value.strip())
            continue

        fields = stripped.split()
        if header is None:
            header = fields
            missing = [name for name in REQUIRED_COLUMNS if name not in header]
            if missing:
                raise ValueError(
                    f"Scan table {path} is missing required columns: "
                    + ", ".join(missing)
                )
            if len(set(header)) != len(header):
                raise ValueError(f"Scan table {path} has duplicate column names")
            columns = {name: [] for name in header}
            continue

        if len(fields) != len(header):
            raise ValueError(
                f"Expected {len(header)} fields at line {line_number}, "
                f"found {len(fields)}"
            )
        for name, raw_value in zip(header, fields):
            try:
                value: float | int
                if name in {"passing_events", "total_events"}:
                    value = int(raw_value)
                else:
                    value = float(raw_value)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid {name} value at line {line_number}: {raw_value!r}"
                ) from exc
            columns[name].append(value)

    if header is None or not columns["threshold_gev"]:
        raise ValueError(f"No scan points found in {path}")
    return metadata, columns


def _load_companion(
    path: Path,
    text_metadata: Mapping[str, Any],
    calibration_file: str | Path | None,
) -> tuple[Path | None, dict[str, Any]]:
    if calibration_file is not None:
        candidate = Path(calibration_file).expanduser()
        if not candidate.is_file():
            raise FileNotFoundError(f"Calibration JSON does not exist: {candidate}")
        return candidate, json.loads(candidate.read_text(encoding="utf-8"))

    trigger = text_metadata.get("trigger", text_metadata.get("trigger_name"))
    method = text_metadata.get("method")
    if trigger and method:
        candidate = path.parent / f"{trigger}_{method}_calibrated_threshold.json"
        if candidate.is_file():
            return candidate, json.loads(candidate.read_text(encoding="utf-8"))

    matches: list[tuple[Path, dict[str, Any]]] = []
    for candidate in sorted(path.parent.glob("*_calibrated_threshold.json")):
        try:
            content = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if trigger and content.get("trigger_name") != trigger:
            continue
        if method and content.get("method") != method:
            continue
        matches.append((candidate, content))
    if len(matches) == 1:
        return matches[0]
    return None, {}


def _number(metadata: Mapping[str, Any], key: str) -> float | None:
    value = metadata.get(key)
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Metadata {key!r} must be numeric; found {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"Metadata {key!r} must be finite; found {value!r}")
    return result


def _infer_collision_rate_mhz(
    efficiencies: tuple[float, ...], rates_khz: tuple[float, ...]
) -> float:
    estimates = [
        rate / efficiency / 1000.0
        for efficiency, rate in zip(efficiencies, rates_khz)
        if efficiency > 0.0 and rate > 0.0
    ]
    if not estimates:
        raise ValueError(
            "Cannot infer the collision rate from an all-zero scan; add "
            "collision_rate_mhz metadata or provide its calibration JSON"
        )
    inferred = median(estimates)
    if any(not math.isclose(value, inferred, rel_tol=1e-7, abs_tol=1e-9)
           for value in estimates):
        raise ValueError("rate_khz and efficiency imply inconsistent collision rates")
    return inferred


def _default_reference_metadata(trigger_name: str) -> dict[str, Any]:
    """Recover menu fields for legacy scans with no rich text header."""
    try:
        from collide2v_trigger.calibration.threshold_scan import (
            build_threshold_scan_plan,
        )
        from collide2v_trigger.core.configurable_menu import (
            ConfigurableCollide2VTriggerMenu,
        )

        plan = build_threshold_scan_plan(
            ConfigurableCollide2VTriggerMenu(), trigger_name
        )
    except (KeyError, TypeError, ValueError):
        return {}
    return {
        "reference_field": plan.reference_field,
        "scale_fields": list(plan.scale_fields),
        "base_thresholds_gev": dict(plan.base_thresholds),
        "threshold_profile": "default",
    }


def _validate_points(
    thresholds: tuple[float, ...],
    efficiencies: tuple[float, ...],
    rates: tuple[float, ...],
    passing: tuple[int, ...],
    totals: tuple[int, ...],
    collision_rate_mhz: float,
) -> None:
    if any(not math.isfinite(value) for value in thresholds + efficiencies + rates):
        raise ValueError("Scan table contains a non-finite threshold, efficiency, or rate")
    if any(right <= left for left, right in zip(thresholds, thresholds[1:])):
        raise ValueError("threshold_gev values must be strictly increasing")
    if any(not 0.0 <= value <= 1.0 for value in efficiencies):
        raise ValueError("efficiency values must lie in [0, 1]")
    if any(value < 0.0 for value in rates):
        raise ValueError("rate_khz values cannot be negative")
    if any(right > left + 1e-12 for left, right in zip(efficiencies, efficiencies[1:])):
        raise ValueError("efficiency must be nonincreasing with threshold")
    if any(right > left + 1e-9 for left, right in zip(rates, rates[1:])):
        raise ValueError("rate_khz must be nonincreasing with threshold")
    if len(set(totals)) != 1 or totals[0] <= 0:
        raise ValueError("total_events must be one shared positive event count")
    for efficiency, rate, accepted, total in zip(
        efficiencies, rates, passing, totals
    ):
        if not 0 <= accepted <= total:
            raise ValueError("passing_events must lie between zero and total_events")
        if not math.isclose(efficiency, accepted / total, abs_tol=5e-12):
            raise ValueError("efficiency is inconsistent with the event counts")
        expected_rate = efficiency * collision_rate_mhz * 1000.0
        if not math.isclose(rate, expected_rate, rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError(
                "rate_khz is inconsistent with efficiency and collision_rate_mhz"
            )


def read_threshold_scan(
    input_file: str | Path,
    *,
    calibration_file: str | Path | None = None,
    selected_calibration_file: str | Path | None = None,
) -> ThresholdRateScan:
    """Parse a scan table and automatically recover its physics metadata.

    Rich ``rate-scan-points-v2`` files are self-contained. For older files,
    the function looks for ``<trigger>_<method>_calibrated_threshold.json`` in
    the same directory and then fills any remaining trigger-threshold metadata
    from the default menu. ``selected_calibration_file`` can mark a bisection
    result on a monotonic curve without replacing that curve's metadata.
    """
    path = Path(input_file).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Threshold scan file does not exist: {path}")

    text_metadata, columns = _read_table(path)
    companion_path, companion = _load_companion(
        path, text_metadata, calibration_file
    )
    trigger_name = str(
        text_metadata.get("trigger", text_metadata.get("trigger_name", ""))
        or companion.get("trigger_name", "")
    )
    if not trigger_name:
        raise ValueError("Scan metadata does not identify a trigger")

    merged: dict[str, Any] = {}
    merged.update(_default_reference_metadata(trigger_name))
    merged.update(companion)
    merged.update(text_metadata)
    for key in ("method", "target_rate_khz"):
        if key in text_metadata and key in companion:
            left = text_metadata[key]
            right = companion[key]
            if str(left) != str(right) and not (
                isinstance(left, (int, float))
                and isinstance(right, (int, float))
                and math.isclose(float(left), float(right))
            ):
                raise ValueError(
                    f"Scan text and calibration JSON disagree on {key}: "
                    f"{left!r} versus {right!r}"
                )

    thresholds = tuple(float(value) for value in columns["threshold_gev"])
    efficiencies = tuple(float(value) for value in columns["efficiency"])
    rates = tuple(float(value) for value in columns["rate_khz"])
    passing = tuple(int(value) for value in columns["passing_events"])
    totals = tuple(int(value) for value in columns["total_events"])
    collision_rate_mhz = _number(merged, "collision_rate_mhz")
    if collision_rate_mhz is None:
        collision_rate_mhz = _infer_collision_rate_mhz(efficiencies, rates)
    if collision_rate_mhz <= 0.0:
        raise ValueError("collision_rate_mhz must be positive")
    _validate_points(
        thresholds, efficiencies, rates, passing, totals, collision_rate_mhz
    )

    target_rate = _number(merged, "target_rate_khz")
    if target_rate is None:
        raise ValueError("Scan metadata does not contain target_rate_khz")
    selected_threshold = _number(merged, "calibrated_threshold_gev")
    selected_rate = _number(merged, "measured_rate_khz")
    selected_efficiency = _number(merged, "measured_efficiency")
    selected_passing_value = merged.get("passing_events")
    if selected_threshold is None:
        policy = str(merged.get("selection_policy", "at_most"))
        if policy == "at_most":
            candidates = [i for i, value in enumerate(rates) if value <= target_rate]
            selected_index = candidates[0] if candidates else len(rates) - 1
        else:
            selected_index = min(
                range(len(rates)),
                key=lambda index: (
                    abs(rates[index] - target_rate),
                    rates[index] > target_rate,
                    thresholds[index],
                ),
            )
        selected_threshold = thresholds[selected_index]
        selected_rate = rates[selected_index]
        selected_efficiency = efficiencies[selected_index]
        selected_passing_value = passing[selected_index]
    else:
        selected_index = min(
            range(len(thresholds)),
            key=lambda index: abs(thresholds[index] - selected_threshold),
        )
        if selected_rate is None:
            selected_rate = rates[selected_index]
        if selected_efficiency is None:
            selected_efficiency = efficiencies[selected_index]
        if selected_passing_value is None:
            selected_passing_value = passing[selected_index]

    base_raw = merged.get("base_thresholds_gev", {})
    if not isinstance(base_raw, Mapping):
        raise ValueError("base_thresholds_gev metadata must be an object")
    base_thresholds = {str(key): float(value) for key, value in base_raw.items()}
    scale_raw = merged.get("scale_fields", [])
    if isinstance(scale_raw, str):
        scale_fields = (scale_raw,)
    elif isinstance(scale_raw, list):
        scale_fields = tuple(str(value) for value in scale_raw)
    else:
        raise ValueError("scale_fields metadata must be a list")

    rate_resolution = _number(merged, "rate_resolution_khz")
    if rate_resolution is None:
        rate_resolution = collision_rate_mhz * 1000.0 / totals[0]
    reference_field = str(merged.get("reference_field", "threshold"))
    scan = ThresholdRateScan(
        source_file=path,
        calibration_file=companion_path,
        trigger_name=trigger_name,
        method=str(merged.get("method", "unknown")),
        reference_field=reference_field,
        scale_fields=scale_fields,
        base_thresholds_gev=base_thresholds,
        target_rate_khz=target_rate,
        collision_rate_mhz=collision_rate_mhz,
        selected_threshold_gev=selected_threshold,
        selected_efficiency=float(selected_efficiency),
        selected_rate_khz=float(selected_rate),
        selected_passing_events=int(selected_passing_value),
        total_events=totals[0],
        rate_resolution_khz=rate_resolution,
        selection_policy=str(merged.get("selection_policy", "at_most")),
        threshold_profile=str(merged.get("threshold_profile", "unknown")),
        thresholds_gev=thresholds,
        efficiencies=efficiencies,
        rates_khz=rates,
        passing_events=passing,
        metadata=merged,
    )
    if selected_calibration_file is not None:
        selected_path = Path(selected_calibration_file).expanduser()
        chosen = json.loads(selected_path.read_text(encoding="utf-8"))
        for key, expected in {
            "trigger_name": scan.trigger_name,
            "reference_field": scan.reference_field,
            "threshold_profile": scan.threshold_profile,
            "scale_fields": list(scan.scale_fields),
            "base_thresholds_gev": dict(scan.base_thresholds_gev),
            "total_events": scan.total_events,
            "target_rate_khz": scan.target_rate_khz,
            "collision_rate_mhz": scan.collision_rate_mhz,
        }.items():
            if chosen.get(key) != expected:
                raise ValueError(f"Selected calibration disagrees with the curve on {key}")
        if "input_score_files" in merged and chosen.get("input_score_files") != merged["input_score_files"]:
            raise ValueError("Selected calibration and curve use different score files")
        threshold = _number(chosen, "calibrated_threshold_gev")
        efficiency = _number(chosen, "measured_efficiency")
        rate = _number(chosen, "measured_rate_khz")
        passed = chosen.get("passing_events")
        if threshold is None or efficiency is None or rate is None or not isinstance(passed, int):
            raise ValueError("Selected calibration is missing its threshold, efficiency, rate, or event count")
        if not scan.thresholds_gev[0] <= threshold <= scan.thresholds_gev[-1]:
            raise ValueError("Selected threshold lies outside the curve's scanned range")
        if not 0 <= passed <= scan.total_events or not math.isclose(
            efficiency, passed / scan.total_events, abs_tol=1e-10
        ) or not math.isclose(rate, efficiency * scan.collision_rate_mhz * 1000, abs_tol=1e-8):
            raise ValueError("Selected calibration rate, efficiency, and counts are inconsistent")
        scan = replace(
            scan,
            selected_threshold_gev=threshold,
            selected_efficiency=efficiency,
            selected_rate_khz=rate,
            selected_passing_events=passed,
            selection_policy=str(chosen.get("selection_policy", scan.selection_policy)),
            metadata={
                **merged,
                "selected_method": chosen.get("method", "unknown"),
                "selected_target_bracketed": chosen.get("target_bracketed", True),
                "quality": chosen.get("quality", {}),
            },
        )
    return scan


def _field_label(reference_field: str, trigger_name: str, *, isolation_enabled: bool = True) -> str:
    if reference_field == "met":
        return r"PUPPI $p_T^{miss}$ threshold [GeV]"
    if reference_field == "ht":
        return r"Reference $H_T$ threshold [GeV]"
    if "jet" in reference_field:
        object_name = "PUPPI jet"
    elif "muon" in reference_field:
        object_name = "track muon"
    elif "electron" in reference_field:
        object_name = "track electron"
    elif "photon" in reference_field:
        object_name = "track-isolated photon" if isolation_enabled and "IsoPhoton" in trigger_name else "photon"
    elif "tau" in reference_field:
        object_name = "PUPPI tau" if "Puppi" in trigger_name else "calorimeter tau"
    elif "ht" in reference_field.lower():
        object_name = r"$H_T$"
    else:
        object_name = reference_field.replace("_", " ")
    leg = ""
    if reference_field.startswith("leading_"):
        leg = "leading "
        object_name = object_name.replace("leading ", "")
    elif reference_field.startswith(("second_", "subleading_")):
        leg = "subleading "
        object_name = object_name.replace("second ", "").replace("subleading ", "")
    return rf"Reference {leg}{object_name} $p_T$ threshold [GeV]"


def _auto_x_limits(scan: ThresholdRateScan) -> tuple[float, float]:
    lower = scan.thresholds_gev[0]
    positive = [
        threshold
        for threshold, rate in zip(scan.thresholds_gev, scan.rates_khz)
        if rate > 0.0
    ]
    last_positive = max(positive[-1] if positive else lower, scan.selected_threshold_gev)
    span = max(last_positive - lower, scan.selected_threshold_gev - lower, 1.0)
    upper = min(scan.thresholds_gev[-1], last_positive + max(0.12 * span, 2.0))
    if upper <= lower:
        upper = scan.thresholds_gev[-1]
    return lower, upper


def plot_threshold_scan(
    input_file: str | Path,
    output_file: str | Path | None = None,
    *,
    calibration_file: str | Path | None = None,
    selected_calibration_file: str | Path | None = None,
    full_range: bool = False,
    x_min: float | None = None,
    x_max: float | None = None,
    log_y: bool = True,
    dpi: int = 200,
    overwrite: bool = False,
) -> Path:
    """Plot measured trigger rate and efficiency against the scanned threshold.

    Metadata, including the target and selected rates, collision rate, reference
    threshold field, event count, and default menu threshold, is read from the
    text header or its automatically discovered calibration JSON. Supply
    ``selected_calibration_file`` to mark a separate bisection calibration on
    the same monotonic curve.
    """
    if dpi <= 0:
        raise ValueError("dpi must be positive")
    scan = read_threshold_scan(
        input_file, calibration_file=calibration_file,
        selected_calibration_file=selected_calibration_file,
    )
    output = (
        Path(output_file).expanduser()
        if output_file is not None
        else scan.source_file.with_suffix(".png")
    )
    if output.resolve() == scan.source_file.resolve():
        raise ValueError("Output image must differ from the scan text file")
    if output.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {output}; pass overwrite=True to replace it"
        )

    if full_range:
        display_min, display_max = scan.thresholds_gev[0], scan.thresholds_gev[-1]
    else:
        display_min, display_max = _auto_x_limits(scan)
    if x_min is not None:
        display_min = float(x_min)
    if x_max is not None:
        display_max = float(x_max)
    if not math.isfinite(display_min) or not math.isfinite(display_max):
        raise ValueError("x-axis limits must be finite")
    if display_min >= display_max:
        raise ValueError("x_min must be smaller than x_max")

    fig, ax = plt.subplots(figsize=(10.5, 7.2))
    rate_floor = max(scan.rate_resolution_khz / 2.0, 1e-12)
    if scan.target_rate_khz > 0.0:
        rate_floor = min(rate_floor, scan.target_rate_khz / 2.0)
    displayed_rates = [
        rate if rate > 0.0 or not log_y else rate_floor
        for rate in scan.rates_khz
    ]
    ax.step(
        scan.thresholds_gev,
        displayed_rates,
        where="post",
        color="#3677b5",
        linewidth=2.2,
        label="Measured rate",
    )
    positive_x = [
        threshold
        for threshold, rate in zip(scan.thresholds_gev, scan.rates_khz)
        if rate > 0.0
    ]
    positive_y = [rate for rate in scan.rates_khz if rate > 0.0]
    ax.scatter(positive_x, positive_y, color="#3677b5", s=12, zorder=3)
    zero_x = [
        threshold
        for threshold, rate in zip(scan.thresholds_gev, scan.rates_khz)
        if rate == 0.0 and display_min <= threshold <= display_max
    ]
    if zero_x and log_y:
        ax.scatter(
            zero_x,
            [rate_floor] * len(zero_x),
            marker="v",
            s=26,
            color="#7796b3",
            zorder=3,
            label=f"0 observed (drawn at {rate_floor:g} kHz)",
        )

    target_efficiency = (
        scan.target_rate_khz / (scan.collision_rate_mhz * 1000.0)
    )
    ax.axhline(
        scan.target_rate_khz,
        color="#d45500",
        linestyle="--",
        linewidth=1.7,
        label=(
            f"Target: {scan.target_rate_khz:g} kHz "
            f"({100.0 * target_efficiency:.4f}%)"
        ),
    )
    ax.axvline(
        scan.selected_threshold_gev,
        color="#16824b",
        linestyle=":",
        linewidth=2.0,
        label=(f"Selected ({scan.metadata.get('selected_method', scan.method)}): "
               f"{scan.selected_threshold_gev:g} GeV"),
    )
    selected_y = (
        max(scan.selected_rate_khz, rate_floor)
        if log_y
        else scan.selected_rate_khz
    )
    ax.scatter(
        [scan.selected_threshold_gev],
        [selected_y],
        color="#16824b",
        edgecolor="white",
        linewidth=0.8,
        s=75,
        zorder=5,
    )
    annotation = (
        f"{scan.selected_threshold_gev:g} GeV\n"
        f"{scan.selected_rate_khz:g} kHz, "
        f"{100.0 * scan.selected_efficiency:.4f}%\n"
        f"{scan.selected_passing_events}/{scan.total_events} events"
    )
    annotate_left = (
        scan.selected_threshold_gev > display_min + 0.75 * (display_max - display_min)
    )
    annotate_below = selected_y > 0.5 * max(
        positive_y + [scan.target_rate_khz, selected_y, rate_floor]
    )
    ax.annotate(
        annotation,
        xy=(scan.selected_threshold_gev, selected_y),
        xytext=(-14 if annotate_left else 14, -14 if annotate_below else 14),
        textcoords="offset points",
        fontsize=12,
        fontweight="semibold",
        ha="right" if annotate_left else "left",
        va="top" if annotate_below else "bottom",
        bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "alpha": 0.9,
              "edgecolor": "#16824b"},
        arrowprops={"arrowstyle": "->", "color": "#16824b"},
    )

    default_threshold = scan.base_thresholds_gev.get(scan.reference_field)
    if default_threshold is not None and display_min <= default_threshold <= display_max:
        ax.axvline(
            default_threshold,
            color="#666666",
            linestyle="-.",
            linewidth=1.4,
            label=f"Menu default: {default_threshold:g} GeV",
        )

    if log_y:
        ax.set_yscale("log")
        positive_visible = [
            rate for threshold, rate in zip(scan.thresholds_gev, scan.rates_khz)
            if display_min <= threshold <= display_max and rate > 0.0
        ]
        upper_rate = max(positive_visible + [scan.target_rate_khz, selected_y, rate_floor])
        ax.set_ylim(rate_floor * 0.75, upper_rate * 1.8)
    else:
        ax.set_ylim(bottom=0.0)
    ax.set_xlim(display_min, display_max)
    ax.set_xlabel(_field_label(scan.reference_field, scan.trigger_name,
                              isolation_enabled=scan.metadata.get("isolation_enabled", True)), fontsize=12)
    ax.set_ylabel("Measured trigger rate [kHz]", fontsize=12)
    ax.set_title(
        f"{scan.trigger_name} threshold calibration\n"
        f"{scan.method.capitalize()} scan of unweighted minimum-bias events",
        fontsize=15,
        pad=12,
    )
    ax.grid(True, which="both" if log_y else "major", linestyle="--", alpha=0.3)
    ax.set_axisbelow(True)
    collision_rate_khz = scan.collision_rate_mhz * 1000.0
    efficiency_axis = ax.secondary_yaxis(
        "right",
        functions=(
            lambda rate: 100.0 * rate / collision_rate_khz,
            lambda percent: percent * collision_rate_khz / 100.0,
        ),
    )
    efficiency_axis.set_ylabel(
        f"Efficiency [%] at {scan.collision_rate_mhz:g} MHz", fontsize=12
    )

    info_lines = [
        f"Events: {scan.total_events:,} (unweighted)",
        f"Rate granularity: {scan.rate_resolution_khz:g} kHz/event",
        f"Selection: {scan.selection_policy}",
    ]
    quality = scan.metadata.get("quality", {})
    if quality:
        info_lines.append(f"Fit checks: {quality['status']} (sample only)")
    if not scan.metadata.get("selected_target_bracketed", scan.metadata.get("target_bracketed", True)):
        info_lines.append("Target unreachable in scanned range; boundary shown")
    if default_threshold is not None:
        info_lines.append(f"Menu {scan.reference_field}: {default_threshold:g} GeV")
    if scan.metadata.get("isolation_enabled") is False:
        info_lines.append("Built-in isolation cuts: disabled")
    if len(scan.scale_fields) > 1:
        info_lines.append("Scaled legs: " + ", ".join(scan.scale_fields))
    external = scan.metadata.get("trigger_definition", {}).get("definition", {})
    fixed = external.get("fixed_parameters", {})
    if "mass_min_gev" in fixed and "mass_max_gev" in fixed:
        info_lines.append(f"Mass window: [{fixed['mass_min_gev']:g}, {fixed['mass_max_gev']:g}] GeV")
    if not full_range and (
        display_min > scan.thresholds_gev[0]
        or display_max < scan.thresholds_gev[-1]
    ):
        info_lines.append(
            f"Displayed range: {display_min:g}–{display_max:g} GeV "
            f"(scan: {scan.thresholds_gev[0]:g}–{scan.thresholds_gev[-1]:g})"
        )
    ax.text(
        0.985,
        0.975,
        "\n".join(info_lines),
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=9,
        bbox={"boxstyle": "round,pad=0.4", "facecolor": "white", "alpha": 0.88,
              "edgecolor": "#999999"},
    )
    legend_right = (
        scan.selected_threshold_gev < display_min + 0.35 * (display_max - display_min)
        and selected_y < rate_floor * 3
    )
    ax.legend(loc="lower right" if legend_right else "lower left", fontsize=9, framealpha=0.9)

    warning_margin = 0.0
    if quality.get("flags"):
        warning = "REVIEW REQUIRED: " + "; ".join(
            flag.replace("_", " ") for flag in quality["flags"]
        )
        if "rate_mismatch" in quality["flags"]:
            warning += (
                f". Rate difference: {quality['rate_difference_khz']:+g} kHz; "
                f"reporting tolerance: {quality['effective_rate_tolerance_khz']:g} kHz."
            )
        lines = textwrap.wrap(warning, width=110)
        warning_margin = 0.025 + 0.022 * len(lines)
        fig.text(
            0.5, 0.01, "\n".join(lines), ha="center", va="bottom",
            fontsize=9, color="#953b00",
            bbox={"boxstyle": "round,pad=0.4", "facecolor": "#fff2df", "edgecolor": "#cb7400"},
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, warning_margin, 1, 1))
    fig.savefig(output, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return output


__all__ = ["ThresholdRateScan", "plot_threshold_scan", "read_threshold_scan"]
