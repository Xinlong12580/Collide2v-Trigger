#!/usr/bin/env python3
"""Plot a saved threshold scan with automatically parsed physics metadata."""

from __future__ import annotations

import argparse
from pathlib import Path

from collide2v_trigger.plotting import plot_threshold_scan, read_threshold_scan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Threshold scan-points text file")
    parser.add_argument(
        "-o", "--output", type=Path, help="Output image (default: <input stem>.png)"
    )
    parser.add_argument(
        "--calibration-json",
        type=Path,
        help="Calibration JSON (normally discovered automatically)",
    )
    parser.add_argument(
        "--selected-calibration-json",
        type=Path,
        help="Mark a separate bisection result on the curve",
    )
    parser.add_argument(
        "--full-range",
        action="store_true",
        help="Show the complete scanned threshold range, including a zero-only tail",
    )
    parser.add_argument("--x-min", type=float, metavar="GEV")
    parser.add_argument("--x-max", type=float, metavar="GEV")
    parser.add_argument(
        "--linear-y", action="store_true", help="Use a linear rate axis"
    )
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument(
        "--overwrite", action="store_true", help="Replace an existing output image"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scan = read_threshold_scan(
        args.input, calibration_file=args.calibration_json,
        selected_calibration_file=args.selected_calibration_json,
    )
    output = plot_threshold_scan(
        args.input,
        args.output,
        calibration_file=args.calibration_json,
        selected_calibration_file=args.selected_calibration_json,
        full_range=args.full_range,
        x_min=args.x_min,
        x_max=args.x_max,
        log_y=not args.linear_y,
        dpi=args.dpi,
        overwrite=args.overwrite,
    )
    companion = str(scan.calibration_file) if scan.calibration_file else "not needed"
    print(f"Trigger: {scan.trigger_name}")
    print(
        f"Selected {scan.reference_field}: {scan.selected_threshold_gev:g} GeV; "
        f"{scan.selected_rate_khz:g} kHz; "
        f"{100.0 * scan.selected_efficiency:.4f}%"
    )
    print(f"Calibration metadata: {companion}")
    print(f"Wrote plot: {output}")


if __name__ == "__main__":
    main()
