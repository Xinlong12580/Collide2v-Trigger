#!/usr/bin/env python3
"""Run event scoring, per-trigger rate calibration, curves, and menu export."""

from __future__ import annotations

import argparse
from pathlib import Path

from collide2v_trigger.calibration.menu_calibration import calibrate_menu


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_directory", help="EOS root:// URL, /eos/... path, or local directory")
    parser.add_argument("-o", "--output-directory", type=Path, default=Path("results/menu_calibration"))
    parser.add_argument("-n", "--n-files", type=int, default=10, help="Common files per trigger (default: 10)")
    parser.add_argument("--trigger", action="append", dest="triggers", help="Repeat to select seeds (default: all implementable triggers)")
    parser.add_argument("--selection", choices=("random", "first"), default="random")
    parser.add_argument("--random-seed", type=int, default=12345)
    parser.add_argument("--lower-bound", type=float, default=0.0, metavar="GEV")
    parser.add_argument("--upper-bound", type=float, default=1000.0, metavar="GEV")
    parser.add_argument("--score-tolerance", type=float, default=0.25, metavar="GEV", help="Per-event bisection precision (default: 0.25 GeV)")
    parser.add_argument("--tolerance", type=float, metavar="GEV", help="Rate-bisection precision (default: score tolerance)")
    parser.add_argument("--spacing", type=float, default=1.0, metavar="GEV", help="Monotonic curve spacing (default: 1 GeV)")
    parser.add_argument("--collision-rate-mhz", type=float, default=40.0)
    parser.add_argument("--selection-policy", choices=("at_most", "closest"), default="at_most")
    parser.add_argument("--export-unreachable-boundary", action="store_true", help="Export the bisection boundary for unreachable targets, retaining unreachable status and quality flags (default: retain base thresholds)")
    parser.add_argument("--rate-tolerance-relative", type=float, default=0.10, help="Rate-match reporting tolerance (default: 0.10 = 10%%)")
    parser.add_argument("--rate-tolerance-khz", type=float, help="Absolute rate reporting tolerance (default: one event); combined with relative tolerance using max")
    parser.add_argument("--min-passing-events", type=int, default=25, help="Minimum passing events for quality reporting (default: 25; 0 disables)")
    parser.add_argument("--threshold-config", type=Path, help="Optional base threshold profile")
    parser.add_argument("--custom-trigger-file", action="append", type=Path, default=[], help="Python file exporting TRIGGER or TRIGGERS; repeat for multiple files")
    parser.add_argument("--no-isolation", action="store_true", help="Use the menu variant without built-in electron/photon isolation cuts")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--electron-iso-max", type=float, default=0.15)
    parser.add_argument("--photon-iso-max", type=float, default=0.15)
    parser.add_argument("--uncorrected-isolation", action="store_true")
    parser.add_argument("--eos-endpoint", default="root://eosproject.cern.ch")
    parser.add_argument("--no-plots", action="store_true", help="Save rate tables and JSON without images")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--fail-fast", action="store_true", help="Stop on the first error (default: continue other triggers)")
    parser.add_argument("--quiet", action="store_true", help="Hide per-file and per-batch progress")
    args = parser.parse_args()
    result = calibrate_menu(
        args.input_directory,
        args.output_directory,
        triggers=args.triggers,
        n_files=args.n_files,
        selection=args.selection,
        random_seed=args.random_seed,
        lower_bound_gev=args.lower_bound,
        upper_bound_gev=args.upper_bound,
        score_tolerance_gev=args.score_tolerance,
        calibration_tolerance_gev=args.tolerance,
        spacing_gev=args.spacing,
        collision_rate_mhz=args.collision_rate_mhz,
        selection_policy=args.selection_policy,
        export_unreachable_boundary=args.export_unreachable_boundary,
        rate_tolerance_relative=args.rate_tolerance_relative,
        rate_tolerance_khz=args.rate_tolerance_khz,
        min_passing_events=args.min_passing_events,
        threshold_config=args.threshold_config,
        custom_trigger_files=args.custom_trigger_file,
        ignore_isolation=args.no_isolation,
        batch_size=args.batch_size,
        electron_iso_max=args.electron_iso_max,
        photon_iso_max=args.photon_iso_max,
        use_rho_corrected_isolation=not args.uncorrected_isolation,
        eos_endpoint=args.eos_endpoint,
        make_plots=not args.no_plots,
        plot_dpi=args.dpi,
        fail_fast=args.fail_fast,
        progress=not args.quiet,
    )
    if result.failed_triggers:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
