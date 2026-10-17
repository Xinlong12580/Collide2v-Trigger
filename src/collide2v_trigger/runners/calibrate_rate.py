#!/usr/bin/env python3
"""CLI for deriving a rate-matched threshold from score sidecars."""

from __future__ import annotations

import argparse
from pathlib import Path

from collide2v_trigger.calibration.rate_calibration import (
    calibrate_rate_from_threshold_scores,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read event-threshold sidecars, infer their trigger, and find the "
            "threshold corresponding to a requested trigger rate."
        )
    )
    parser.add_argument("input_directory", help="Directory containing score Parquets")
    parser.add_argument(
        "--trigger",
        help="Trigger to use when the directory contains more than one trigger",
    )
    parser.add_argument(
        "--target-rate-khz",
        type=float,
        help="Target rate in kHz (default: listed rate from the trigger menu)",
    )
    parser.add_argument(
        "--method",
        choices=("bisection", "monotonic"),
        default="bisection",
        help="Threshold search method (default: bisection)",
    )
    parser.add_argument(
        "--spacing",
        type=float,
        default=1.0,
        metavar="GEV",
        help="Threshold spacing for --method monotonic (default: 1 GeV)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        metavar="GEV",
        help="Bisection tolerance (default: tolerance stored in score files)",
    )
    parser.add_argument("--lower-bound", type=float, metavar="GEV")
    parser.add_argument("--upper-bound", type=float, metavar="GEV")
    parser.add_argument(
        "--collision-rate-mhz",
        type=float,
        default=40.0,
        help="Collision rate used to convert efficiency to rate (default: 40 MHz)",
    )
    parser.add_argument(
        "--selection-policy",
        choices=("at_most", "closest"),
        default="at_most",
        help=(
            "Choose the lowest scanned threshold at or below the target rate, "
            "or the closest measured rate (default: at_most)"
        ),
    )
    parser.add_argument(
        "--rate-tolerance-relative",
        type=float, default=0.10,
        help="Relative tolerance for the rate-match quality flag (default: 0.10 = 10%%)",
    )
    parser.add_argument(
        "--rate-tolerance-khz", type=float,
        help="Absolute rate reporting tolerance (default: one event); combined with relative tolerance using max",
    )
    parser.add_argument(
        "--min-passing-events", type=int, default=25,
        help="Flag fits with fewer passing events (default: 25; 0 disables this check)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Calibration JSON (default: written inside the input directory)",
    )
    parser.add_argument(
        "--scan-points",
        type=Path,
        metavar="TXT",
        help="Optionally save every evaluated threshold and rate to a text file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    calibrate_rate_from_threshold_scores(
        input_directory=args.input_directory,
        trigger_name=args.trigger,
        target_rate_khz=args.target_rate_khz,
        method=args.method,
        spacing_gev=args.spacing,
        tolerance_gev=args.tolerance,
        lower_bound_gev=args.lower_bound,
        upper_bound_gev=args.upper_bound,
        collision_rate_mhz=args.collision_rate_mhz,
        selection_policy=args.selection_policy,
        rate_tolerance_relative=args.rate_tolerance_relative,
        rate_tolerance_khz=args.rate_tolerance_khz,
        min_passing_events=args.min_passing_events,
        output_file=args.output,
        scan_points_file=args.scan_points,
    )


if __name__ == "__main__":
    main()
