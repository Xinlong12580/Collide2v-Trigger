"""Plot diphoton invariant masses from 100 minbias files by default."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from collide2v_trigger.calibration.directory_scan import list_parquet_files, select_parquet_files
from collide2v_trigger.calibration.threshold_scan import resolve_input_source
from collide2v_trigger.plotting.diphoton_mass import collect_diphoton_mass_histogram, plot_diphoton_mass


DEFAULT_INPUT = "/eos/project/f/foundational-model-dataset/samples/production_final/minbias"


def select_inputs(args) -> tuple[list[str], dict]:
    """Require the requested number, and preserve a calibration manifest's order."""
    if args.n_files <= 0:
        raise ValueError("--n-files must be positive")
    if args.input_manifest:
        if args.input_directory:
            raise ValueError("Choose an input directory or --input-manifest")
        path = args.input_manifest.expanduser().resolve()
        manifest = json.loads(path.read_text(encoding="utf-8"))
        files = manifest.get("selected_files") if isinstance(manifest, dict) else None
        if not isinstance(files, list) or not files or any(not isinstance(x, str) for x in files):
            raise ValueError("Input manifest must contain a nonempty selected_files list")
        resolved = []
        for source in files:
            if "://" not in source and not Path(source).is_absolute():
                source = str(path.parent / source)
            resolved.append(resolve_input_source(source, eos_endpoint=args.eos_endpoint))
        selected = resolved[:args.n_files]
        selection = {"input_manifest": str(path), "selection": "manifest_order"}
    else:
        directory = args.input_directory or DEFAULT_INPUT
        files = list_parquet_files(directory, eos_endpoint=args.eos_endpoint)
        selected = select_parquet_files(files, n_files=args.n_files,
                                        selection=args.selection, random_seed=args.random_seed)
        selection = {"input_directory": directory, "selection": args.selection,
                     "random_seed": args.random_seed}
    if len(selected) < args.n_files:
        raise ValueError(f"Requested {args.n_files} files, but only {len(selected)} are available; "
                         "set --n-files explicitly to use a smaller sample")
    return selected, {**selection, "requested_n_files": args.n_files}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_directory", nargs="?", help=f"Local/EOS directory (default: {DEFAULT_INPUT})")
    parser.add_argument("--input-manifest", "--manifest", type=Path, help="Calibration input_manifest.json; use its first N selected files without relisting EOS")
    parser.add_argument("-n", "--n-files", type=int, default=100)
    parser.add_argument("--selection", choices=("random", "first"), default="random")
    parser.add_argument("--random-seed", type=int, default=12345)
    parser.add_argument("--pair-mode", choices=("all", "leading"), default="all")
    parser.add_argument("--pt-min", type=float, default=0.0, metavar="GEV", help="Common photon pT cut (default: no added cut)")
    parser.add_argument("--eta-max", type=float, help="Optional strict |eta| cut (default: none)")
    parser.add_argument("--bins", type=int, default=100)
    parser.add_argument("--mass-min", type=float, default=0.0, metavar="GEV")
    parser.add_argument("--mass-max", type=float, default=200.0, metavar="GEV")
    parser.add_argument("--collision-rate-mhz", type=float, default=40.0)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--eos-endpoint", default="root://eosproject.cern.ch")
    parser.add_argument("-o", "--output", type=Path, help="Output image (default: results/minbias_diphoton_mass_<N>files.png); JSON saved beside it")
    parser.add_argument("--linear-y", action="store_true")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    output = (args.output or Path(f"results/minbias_diphoton_mass_{args.n_files}files.png")).expanduser()
    if output.suffix.lower() not in {".png", ".pdf", ".svg"}:
        raise ValueError("--output must be .png, .pdf, or .svg")
    report = output.with_suffix(".json")
    for path in (output, report):
        if path.exists() and not args.overwrite:
            raise FileExistsError(f"Output already exists: {path}; use --overwrite")
    if args.dpi <= 0:
        raise ValueError("--dpi must be positive")
    files, selection = select_inputs(args)
    result = collect_diphoton_mass_histogram(
        files, pair_mode=args.pair_mode, pt_min_gev=args.pt_min, eta_max=args.eta_max,
        bins=args.bins, mass_min_gev=args.mass_min, mass_max_gev=args.mass_max,
        collision_rate_mhz=args.collision_rate_mhz, batch_size=args.batch_size, progress=True,
    )
    result["input_selection"] = selection
    plot_diphoton_mass(result, output, dpi=args.dpi, log_y=not args.linear_y, overwrite=args.overwrite)
    temporary = report.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(report)
    print(f"Processed {result['file_count']} files, {result['events']:,} events, {result['pairs']:,} pairs")
    print(f"120–130 GeV: {result['window_pairs']} pairs in {result['window_events']} events; "
          f"{result['window_event_rate_khz']:g} kHz at {args.collision_rate_mhz:g} MHz")
    print(f"Saved plot: {output}\nSaved histogram, selection and file list: {report}")


if __name__ == "__main__":
    main()
