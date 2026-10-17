#!/usr/bin/env python3
"""CLI for one-file, one-trigger event threshold scans."""

from __future__ import annotations

import argparse
from pathlib import Path

from collide2v_trigger.calibration.threshold_scan import scan_pt_thresholds


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find each event's critical threshold for one trigger by bisection "
            "and write a local one-column Parquet sidecar."
        )
    )
    parser.add_argument(
        "input",
        help="Local Parquet, root:// URL, or ordinary /eos/... xrdfs listing entry",
    )
    parser.add_argument("trigger", help="Exact configurable-menu trigger name")
    parser.add_argument("-o", "--output", type=Path, help="Local output Parquet")
    parser.add_argument("--lower-bound", type=float, default=0.0, metavar="GEV")
    parser.add_argument("--upper-bound", type=float, default=1000.0, metavar="GEV")
    parser.add_argument("--tolerance", type=float, default=0.25, metavar="GEV")
    parser.add_argument(
        "--reference-field",
        help="Threshold field represented by the output coordinate",
    )
    parser.add_argument(
        "--scale-field",
        action="append",
        dest="scale_fields",
        metavar="NAME",
        help=(
            "Threshold field to scale; repeat to choose a subset. By default all "
            "rate-setting transverse thresholds move together."
        ),
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
        help="Endpoint prepended to an unmounted /eos/... input path",
    )
    parser.add_argument(
        "--compression",
        choices=("zstd", "snappy", "gzip", "brotli", "none"),
        default="zstd",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = scan_pt_thresholds(
        args.input,
        args.trigger,
        args.output,
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
        f"Saved {result.event_count} {result.trigger_name} thresholds to "
        f"{result.output_path}"
    )
    print(
        f"Reference field: {result.reference_field}; scaled fields: "
        + ", ".join(result.scale_fields)
    )
    print(
        f"Fails at lower bound: {result.fails_at_lower_bound}; "
        f"passes at upper bound: {result.passes_at_upper_bound}"
    )


if __name__ == "__main__":
    main()
