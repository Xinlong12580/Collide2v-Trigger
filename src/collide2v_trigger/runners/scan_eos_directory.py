#!/usr/bin/env python3
"""CLI for scanning a sample of files directly from one EOS directory."""

from __future__ import annotations

import argparse
from pathlib import Path

from collide2v_trigger.calibration.directory_scan import scan_eos_directory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Select N Parquet files from an EOS directory and create one local "
            "event-threshold sidecar for each file."
        )
    )
    parser.add_argument(
        "input_directory",
        help="root:// EOS directory, unmounted /eos/... path, or local directory",
    )
    parser.add_argument("trigger", help="Exact configurable-menu trigger name")
    parser.add_argument(
        "-o",
        "--output-directory",
        type=Path,
        default=Path("results/threshold_scans"),
        help="Local output directory (default: results/threshold_scans)",
    )
    parser.add_argument(
        "-n",
        "--n-files",
        type=int,
        default=10,
        help="Number of files to scan (default: 10)",
    )
    parser.add_argument(
        "--selection",
        choices=("random", "first"),
        default="random",
        help="File selection policy (default: deterministic random sample)",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=12345,
        help="Seed used for random file selection (default: 12345)",
    )
    parser.add_argument(
        "--fail-existing",
        action="store_true",
        help="Fail instead of skipping output files that already exist",
    )
    parser.add_argument("--lower-bound", type=float, default=0.0, metavar="GEV")
    parser.add_argument("--upper-bound", type=float, default=1000.0, metavar="GEV")
    parser.add_argument("--tolerance", type=float, default=0.25, metavar="GEV")
    parser.add_argument("--reference-field")
    parser.add_argument(
        "--scale-field",
        action="append",
        dest="scale_fields",
        metavar="NAME",
        help="Threshold field to scale; repeat to choose a subset",
    )
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--electron-iso-max", type=float, default=0.15)
    parser.add_argument("--photon-iso-max", type=float, default=0.15)
    parser.add_argument("--uncorrected-isolation", action="store_true")
    parser.add_argument("--threshold-config", type=Path)
    parser.add_argument("--custom-trigger-file", action="append", type=Path, default=[], help="Python file exporting TRIGGER or TRIGGERS; repeat for multiple files")
    parser.add_argument("--no-isolation", action="store_true", help="Use the menu variant without built-in electron/photon isolation cuts")
    parser.add_argument(
        "--eos-endpoint",
        default="root://eosproject.cern.ch",
        help="Endpoint prepended to an unmounted /eos/... directory",
    )
    parser.add_argument(
        "--compression",
        choices=("zstd", "snappy", "gzip", "brotli", "none"),
        default="zstd",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = scan_eos_directory(
        input_directory=args.input_directory,
        trigger_name=args.trigger,
        output_directory=args.output_directory,
        n_files=args.n_files,
        selection=args.selection,
        random_seed=args.random_seed,
        skip_existing=not args.fail_existing,
        lower_bound_gev=args.lower_bound,
        upper_bound_gev=args.upper_bound,
        tolerance_gev=args.tolerance,
        reference_field=args.reference_field,
        scale_fields=args.scale_fields,
        batch_size=args.batch_size,
        electron_iso_max=args.electron_iso_max,
        photon_iso_max=args.photon_iso_max,
        use_rho_corrected_isolation=not args.uncorrected_isolation,
        threshold_config=args.threshold_config,
        custom_trigger_files=args.custom_trigger_file,
        ignore_isolation=args.no_isolation,
        eos_endpoint=args.eos_endpoint,
        compression=None if args.compression == "none" else args.compression,
        progress=True,
    )
    print(
        f"Selected {len(result.selected_files)} of "
        f"{result.available_file_count} ordinary Parquet files"
    )
    print(
        f"Completed {len(result.completed_scans)} scans; "
        f"skipped {len(result.skipped_outputs)} existing outputs"
    )


if __name__ == "__main__":
    main()
