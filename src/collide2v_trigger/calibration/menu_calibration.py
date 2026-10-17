"""Orchestrate per-event scoring, rate calibration, plots, and a menu profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import pyarrow.parquet as pq

from collide2v_trigger.core.external import create_trigger_menu, definition_identity
from collide2v_trigger.io.parquet import is_remote_source, source_stem

from .directory_scan import (
    list_parquet_files,
    resolve_input_directory,
    select_parquet_files,
)
from .rate_calibration import (
    calibrate_rate_from_threshold_scores,
    discover_threshold_score_files,
)
from .quality import quality_criteria
from .threshold_scan import (
    DEFAULT_EOS_ENDPOINT,
    PT_THRESHOLD_COLUMN,
    ThresholdScanPlan,
    build_threshold_scan_plan,
    scan_pt_thresholds,
)


@dataclass(frozen=True)
class MenuCalibrationResult:
    """Output locations and trigger outcomes for a complete or partial run."""

    output_directory: Path
    threshold_config_file: Path
    summary_file: Path
    manifest_file: Path
    selected_files: tuple[str, ...]
    calibrated_triggers: tuple[str, ...]
    unreachable_triggers: tuple[str, ...]
    failed_triggers: tuple[str, ...]
    skipped_triggers: tuple[str, ...]
    flagged_triggers: tuple[str, ...] = ()


def _write_json(path: Path, content: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(content, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _validate_scores(
    directory: Path,
    expected_files: Mapping[Path, str],
    plan: ThresholdScanPlan,
    *,
    profile: str,
    lower: float,
    upper: float,
    tolerance: float,
    trigger_definition: Mapping[str, Any] | None = None,
    isolation_enabled: bool = True,
) -> None:
    """Refuse to resume from unrelated, corrupt, or differently configured scores."""
    actual = set(directory.glob("*.parquet"))
    extra = actual - set(expected_files)
    if extra:
        raise ValueError(f"Unexpected score files in {directory}: {sorted(extra)}")
    expected_metadata = {
        "format": "event-threshold-sidecar-v1",
        "trigger": plan.trigger_name,
        "reference_field": plan.reference_field,
        "threshold_profile": profile,
    }
    for path in actual:
        parquet = pq.ParquetFile(path)
        if parquet.schema_arrow.names != [PT_THRESHOLD_COLUMN]:
            raise ValueError(f"Invalid score columns in {path}")
        metadata = {
            key.decode(): value.decode()
            for key, value in (parquet.schema_arrow.metadata or {}).items()
        }
        stored = json.loads(metadata.get("collide2v_trigger.trigger_definition", "null"))
        if json.loads(metadata.get("collide2v_trigger.isolation_enabled", "true")) != isolation_enabled:
            raise ValueError(f"Score isolation setting does not match this run: {path}")
        if (definition_identity(stored) if stored else None) != (
            definition_identity(trigger_definition) if trigger_definition else None
        ):
            raise ValueError(f"External trigger definition changed in score file: {path}")
        for key, value in {**expected_metadata, "source": expected_files[path]}.items():
            if metadata.get("collide2v_trigger." + key) != value:
                raise ValueError(f"Score metadata {key!r} does not match this run: {path}")
        for key, value in {
            "lower_bound_gev": lower,
            "upper_bound_gev": upper,
            "tolerance_gev": tolerance,
        }.items():
            if float(metadata.get("collide2v_trigger." + key, "nan")) != value:
                raise ValueError(f"Score metadata {key!r} does not match this run: {path}")
        if json.loads(metadata["collide2v_trigger.scale_fields"]) != list(plan.scale_fields):
            raise ValueError(f"Different scaled threshold fields in {path}")
        if json.loads(metadata["collide2v_trigger.base_thresholds_gev"]) != dict(plan.base_thresholds):
            raise ValueError(f"Different base thresholds in {path}")


def calibrate_menu(
    input_directory: str | Path,
    output_directory: str | Path = "results/menu_calibration",
    *,
    triggers: Sequence[str] | None = None,
    n_files: int = 10,
    selection: str = "random",
    random_seed: int = 12345,
    lower_bound_gev: float = 0.0,
    upper_bound_gev: float = 1000.0,
    score_tolerance_gev: float = 0.25,
    calibration_tolerance_gev: float | None = None,
    spacing_gev: float = 1.0,
    collision_rate_mhz: float = 40.0,
    selection_policy: str = "at_most",
    export_unreachable_boundary: bool = False,
    rate_tolerance_relative: float = 0.10,
    rate_tolerance_khz: float | None = None,
    min_passing_events: int = 25,
    target_rates_khz: Mapping[str, float] | None = None,
    threshold_config: str | Path | None = None,
    custom_trigger_files: Sequence[str | Path] = (),
    ignore_isolation: bool = False,
    batch_size: int = 256,
    electron_iso_max: float = 0.15,
    photon_iso_max: float = 0.15,
    use_rho_corrected_isolation: bool = True,
    eos_endpoint: str = DEFAULT_EOS_ENDPOINT,
    make_plots: bool = True,
    plot_dpi: int = 200,
    fail_fast: bool = False,
    progress: bool = True,
) -> MenuCalibrationResult:
    """Calibrate all implementable seeds on the same reproducible file sample.

    Each trigger gets a ``scores/`` directory, bisection calibration, monotonic
    curve, and (by default) plot marking the bisection result. The combined
    ``calibrated_thresholds.json`` can be passed as the menu's ``threshold_config``.
    Multileg thresholds retain their base ratios; fixed construction cuts retain
    their configured values. Unreachable seeds retain their base values unless
    ``export_unreachable_boundary=True`` exports the bisection boundary instead.
    Their status and quality flags remain unresolved. Failed seeds always retain
    their base values.

    Repeating a run reuses complete score files, then regenerates the inexpensive
    rate scans and plots. The manifest locks the file sample and scoring settings;
    rate targets, calibration tolerance, curve spacing, plotting and boundary-export
    options may change without rescoring. EOS is read directly and full events are never copied.
    Quality flags are advisory and do not change the search or exported cuts.
    """
    if not isinstance(export_unreachable_boundary, bool):
        raise TypeError("export_unreachable_boundary must be a boolean")
    reporting_criteria = quality_criteria(
        rate_tolerance_relative=rate_tolerance_relative,
        rate_tolerance_khz=rate_tolerance_khz,
        min_passing_events=min_passing_events,
    )
    if not (0 <= lower_bound_gev < upper_bound_gev) or not all(
        math.isfinite(value) for value in (lower_bound_gev, upper_bound_gev)
    ):
        raise ValueError("Require finite bounds with 0 <= lower_bound_gev < upper_bound_gev")
    for key, value in {
        "score_tolerance_gev": score_tolerance_gev,
        "spacing_gev": spacing_gev,
        "collision_rate_mhz": collision_rate_mhz,
    }.items():
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be finite and positive")
    tolerance = (
        score_tolerance_gev
        if calibration_tolerance_gev is None else calibration_tolerance_gev
    )
    if not math.isfinite(tolerance) or tolerance < score_tolerance_gev:
        raise ValueError("Calibration tolerance must be at least the event-score tolerance")
    if n_files <= 0 or batch_size <= 0 or plot_dpi <= 0:
        raise ValueError("n_files, batch_size, and plot_dpi must be positive")
    if selection not in {"random", "first"}:
        raise ValueError("selection must be 'random' or 'first'")
    if selection_policy not in {"at_most", "closest"}:
        raise ValueError("selection_policy must be 'at_most' or 'closest'")
    if is_remote_source(output_directory):
        raise ValueError("output_directory must be local")

    menu = create_trigger_menu(
        ignore_isolation=ignore_isolation,
        custom_trigger_files=custom_trigger_files,
        threshold_config=threshold_config,
        electron_iso_max=electron_iso_max,
        photon_iso_max=photon_iso_max,
        use_rho_corrected_isolation=use_rho_corrected_isolation,
    )
    requested = list(menu.implementable_triggers if triggers is None else dict.fromkeys(triggers))
    if not requested:
        raise ValueError("Select at least one trigger")
    plans = {name: build_threshold_scan_plan(menu, name) for name in requested}
    target_overrides = dict(target_rates_khz or {})
    if set(target_overrides) - set(requested):
        raise ValueError("Target-rate overrides must refer to selected triggers")
    targets = {
        name: float(target_overrides.get(name, menu.target_rate_khz(name)))
        for name in requested
    }
    if any(not math.isfinite(value) or not 0 <= value <= collision_rate_mhz * 1000
           for value in targets.values()):
        raise ValueError("Each target rate must lie between zero and the collision rate in kHz")
    plot_function = None
    if make_plots:
        try:
            from collide2v_trigger.plotting import plot_threshold_scan
        except ImportError as exc:
            raise RuntimeError("Plotting requires the 'plots' extra; install .[plots]") from exc
        plot_function = plot_threshold_scan

    directory = resolve_input_directory(input_directory, eos_endpoint=eos_endpoint)
    if not is_remote_source(directory):
        directory = str(Path(directory).resolve())
    root = Path(output_directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "input_manifest.json"
    run_settings = {
        "input_directory": directory,
        "requested_n_files": n_files,
        "selection": selection,
        "random_seed": random_seed,
        "triggers": requested,
        "lower_bound_gev": lower_bound_gev,
        "upper_bound_gev": upper_bound_gev,
        "score_tolerance_gev": score_tolerance_gev,
        "threshold_profile": menu.threshold_profile,
        "base_thresholds_gev": menu.effective_thresholds,
        "electron_iso_max": electron_iso_max,
        "photon_iso_max": photon_iso_max,
        "use_rho_corrected_isolation": use_rho_corrected_isolation,
    }
    # Omit the default setting so pre-existing standard-menu manifests still resume.
    if not menu.isolation_enabled:
        run_settings["isolation_enabled"] = False
    if menu.custom_trigger_provenance:
        run_settings["custom_trigger_definitions"] = {
            name: definition_identity(value)
            for name, value in menu.custom_trigger_provenance.items()
        }
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("settings") != run_settings:
            raise ValueError(
                "The existing manifest has different file-selection or scoring settings. "
                "Use a new output directory for a different run."
            )
        selected = manifest["selected_files"]
        if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected):
            raise ValueError("The existing input manifest has an invalid selected_files list")
    else:
        if any(root.iterdir()):
            raise ValueError("A new run requires an empty output directory or its existing manifest")
        available = list_parquet_files(directory, eos_endpoint=eos_endpoint)
        selected = select_parquet_files(
            available, n_files=n_files, selection=selection, random_seed=random_seed
        )
        manifest = {
            "schema_version": 1,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "settings": run_settings,
            "available_file_count": len(available),
            "selected_files": selected,
        }
        _write_json(manifest_path, manifest)
    if len({source_stem(source) for source in selected}) != len(selected):
        raise ValueError("Selected source filenames would produce colliding score filenames")

    config_path = root / "calibrated_thresholds.json"
    summary_path = root / "calibration_summary.json"
    thresholds = menu.effective_thresholds
    skipped = {
        name: menu.NOT_IMPLEMENTABLE_REASONS.get(name, "Not selected")
        for name in menu.all_triggers if name not in requested
    }
    outcomes: dict[str, dict[str, Any]] = {
        name: {"status": "pending", "target_rate_khz": targets[name]}
        for name in requested
    }

    def save_progress() -> None:
        calibrated = [name for name in requested if outcomes[name]["status"] == "calibrated"]
        unresolved = {name: value["status"] for name, value in outcomes.items()
                      if value["status"] != "calibrated"}
        quality = {
            name: value.get("quality", {"status": "not_assessed", "flags": []})
            for name, value in outcomes.items()
        }
        flagged = {name: value["flags"] for name, value in quality.items() if value["flags"]}
        boundary_exports = [name for name in requested
                            if outcomes[name].get("boundary_threshold_exported", False)]
        export_policy = (
            "export boundary thresholds for unreachable triggers; retain base-profile thresholds for failed triggers"
            if export_unreachable_boundary else "retain base-profile thresholds"
        )
        review_required = any(value["status"] != "ok" for value in quality.values())
        config = {
            "schema_version": 1,
            "profile": "minbias_rate_calibrated",
            "collision_rate_mhz": collision_rate_mhz,
            "source_threshold_profile": menu.threshold_profile,
            "calibration_method": "bisection",
            "score_tolerance_gev": score_tolerance_gev,
            "calibration_tolerance_gev": tolerance,
            "selection_policy": selection_policy,
            "export_unreachable_boundary": export_unreachable_boundary,
            "exported_boundary_triggers": boundary_exports,
            "target_rates_khz": targets,
            "quality_criteria": reporting_criteria,
            "calibration_quality": quality,
            "flagged_triggers": flagged,
            "quality_review_required": review_required,
            "manifest_file": str(manifest_path),
            "summary_file": str(summary_path),
            "object_selection": {
                "isolation_enabled": menu.isolation_enabled,
                "electron_iso_max": electron_iso_max,
                "photon_iso_max": photon_iso_max,
                "use_rho_corrected_isolation": use_rho_corrected_isolation,
            },
            "calibrated_triggers": calibrated,
            "uncalibrated_triggers": unresolved,
            "skipped_triggers": skipped,
            "calibration_complete": not unresolved,
            "uncalibrated_threshold_policy": export_policy,
            "thresholds": thresholds,
        }
        if menu.custom_trigger_provenance:
            config["custom_trigger_definitions"] = menu.custom_trigger_provenance
        _write_json(config_path, config)
        _write_json(summary_path, {
            "export_unreachable_boundary": export_unreachable_boundary,
            "exported_boundary_triggers": boundary_exports,
            "uncalibrated_threshold_policy": export_policy,
            "isolation_enabled": menu.isolation_enabled,
            "schema_version": 1,
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            "manifest_file": str(manifest_path),
            "threshold_config_file": str(config_path),
            "collision_rate_mhz": collision_rate_mhz,
            "selected_files": selected,
            "calibration_tolerance_gev": tolerance,
            "monotonic_spacing_gev": spacing_gev,
            "efficiency_weighting": "unweighted events",
            "quality_criteria": reporting_criteria,
            "flagged_triggers": flagged,
            "quality_review_required": review_required,
            "triggers": outcomes,
            "skipped_triggers": skipped,
            "custom_trigger_definitions": menu.custom_trigger_provenance,
        })

    save_progress()
    print(f"Using {len(selected)} common files for {len(requested)} triggers; manifest: {manifest_path}", flush=True)
    for trigger_index, name in enumerate(requested, start=1):
        print(f"[{trigger_index}/{len(requested)}] {name}", flush=True)
        trigger_root = root / name
        score_directory = trigger_root / "scores"
        score_directory.mkdir(parents=True, exist_ok=True)
        expected = {
            score_directory / f"{source_stem(source)}_{name}_pt_threshold.parquet": source
            for source in selected
        }
        outcome = outcomes[name]
        outcome["status"] = "running"
        save_progress()
        try:
            _validate_scores(
                score_directory, expected, plans[name], profile=menu.threshold_profile,
                lower=lower_bound_gev, upper=upper_bound_gev, tolerance=score_tolerance_gev,
                trigger_definition=menu.custom_trigger_provenance.get(name),
                isolation_enabled=menu.isolation_enabled,
            )
            resumed = 0
            for file_index, (path, source) in enumerate(expected.items(), start=1):
                if path.exists():
                    resumed += 1
                    if progress:
                        print(f"  [{file_index}/{len(selected)}] Reusing {path.name}", flush=True)
                    continue
                if progress:
                    print(f"  [{file_index}/{len(selected)}] Scanning {source}", flush=True)
                scan_pt_thresholds(
                    source, name, path,
                    lower_bound_gev=lower_bound_gev,
                    upper_bound_gev=upper_bound_gev,
                    tolerance_gev=score_tolerance_gev,
                    threshold_config=threshold_config,
                    custom_trigger_files=custom_trigger_files,
                    ignore_isolation=ignore_isolation,
                    batch_size=batch_size,
                    electron_iso_max=electron_iso_max,
                    photon_iso_max=photon_iso_max,
                    use_rho_corrected_isolation=use_rho_corrected_isolation,
                    eos_endpoint=eos_endpoint,
                    progress=progress,
                )
            files, _ = discover_threshold_score_files(score_directory, trigger_name=name)
            if set(files) != set(expected):
                raise ValueError("Saved scores do not match the selected input files")
            common = {
                "trigger_name": name,
                "target_rate_khz": targets[name],
                "collision_rate_mhz": collision_rate_mhz,
                "selection_policy": selection_policy,
                "rate_tolerance_relative": rate_tolerance_relative,
                "rate_tolerance_khz": rate_tolerance_khz,
                "min_passing_events": min_passing_events,
            }
            bisection = calibrate_rate_from_threshold_scores(
                score_directory, method="bisection", tolerance_gev=tolerance,
                output_file=trigger_root / "bisection.json",
                scan_points_file=trigger_root / "bisection_points.txt", **common,
            )
            monotonic = calibrate_rate_from_threshold_scores(
                score_directory, method="monotonic", spacing_gev=spacing_gev,
                output_file=trigger_root / "monotonic.json",
                scan_points_file=trigger_root / "monotonic_points.txt", **common,
            )
            fields = dict(plans[name].base_thresholds)
            fields.update(plans[name].thresholds_at(bisection.calibrated_threshold_gev))
            boundary_exported = not bisection.target_bracketed and export_unreachable_boundary
            if bisection.target_bracketed or boundary_exported:
                thresholds[name] = fields
            outcome.update({
                "status": "calibrated" if bisection.target_bracketed else "unreachable",
                "reference_field": bisection.reference_field,
                "scale_fields": list(plans[name].scale_fields),
                "base_thresholds_gev": dict(plans[name].base_thresholds),
                "applied_thresholds_gev": thresholds[name],
                "calibrated_threshold_gev": (
                    bisection.calibrated_threshold_gev if bisection.target_bracketed else None
                ),
                "boundary_threshold_gev": bisection.calibrated_threshold_gev,
                "boundary_threshold_exported": boundary_exported,
                "measured_efficiency": bisection.measured_efficiency,
                "measured_rate_khz": bisection.measured_rate_khz,
                "passing_events": bisection.passing_events,
                "total_events": bisection.total_events,
                "rate_resolution_khz": bisection.rate_resolution_khz,
                "target_bracketed": bisection.target_bracketed,
                "selection_policy": selection_policy,
                "quality": bisection.quality,
                "reused_score_files": resumed,
                "input_score_files": [str(path) for path in files],
                "bisection_file": str(bisection.output_file),
                "monotonic_file": str(monotonic.output_file),
                "monotonic_points_file": str(monotonic.scan_points_file),
            })
            if not bisection.target_bracketed:
                action = "boundary thresholds exported" if boundary_exported else "base thresholds retained"
                outcome["reason"] = "Target rate lies outside the rates covered by the score bounds; " + action
                print(f"  UNREACHABLE: {outcome['reason']}", flush=True)
            if plot_function is not None:
                plot_path = plot_function(
                    monotonic.scan_points_file,
                    trigger_root / "monotonic_points.png",
                    calibration_file=monotonic.output_file,
                    selected_calibration_file=bisection.output_file,
                    dpi=plot_dpi, overwrite=True,
                )
                outcome["plot_file"] = str(plot_path)
        except Exception as exc:
            thresholds[name] = dict(plans[name].base_thresholds)
            outcome.update({
                "status": "failed", "error": f"{type(exc).__name__}: {exc}",
                "applied_thresholds_gev": thresholds[name],
                "calibrated_threshold_gev": None,
                "boundary_threshold_exported": False,
                "quality": {
                    "status": "not_assessed",
                    "flags": ["evaluation_failed"],
                    "messages": [f"Quality could not be assessed: {exc}"],
                },
            })
            print(f"  FAILED: {outcome['error']}", flush=True)
            save_progress()
            if fail_fast:
                raise
        save_progress()

    # Confirm the final artifact is accepted by the public menu constructor.
    create_trigger_menu(threshold_config=config_path, custom_trigger_files=custom_trigger_files,
                        ignore_isolation=ignore_isolation)
    result = MenuCalibrationResult(
        output_directory=root,
        threshold_config_file=config_path,
        summary_file=summary_path,
        manifest_file=manifest_path,
        selected_files=tuple(selected),
        calibrated_triggers=tuple(name for name in requested if outcomes[name]["status"] == "calibrated"),
        unreachable_triggers=tuple(name for name in requested if outcomes[name]["status"] == "unreachable"),
        failed_triggers=tuple(name for name in requested if outcomes[name]["status"] == "failed"),
        skipped_triggers=tuple(skipped),
        flagged_triggers=tuple(
            name for name in requested if outcomes[name].get("quality", {}).get("status") != "ok"
        ),
    )
    print(
        f"Calibrated {len(result.calibrated_triggers)}; "
        f"unreachable {len(result.unreachable_triggers)}; "
        f"failed {len(result.failed_triggers)}; skipped {len(skipped)}\n"
        f"Quality review required: {len(result.flagged_triggers)} triggers\n"
        f"Menu thresholds: {config_path}\nSummary: {summary_path}", flush=True,
    )
    return result


__all__ = ["MenuCalibrationResult", "calibrate_menu"]
