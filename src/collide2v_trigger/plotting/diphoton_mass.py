"""Streaming diphoton mass histograms from original local or EOS Parquets."""

from __future__ import annotations

from itertools import combinations
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from collide2v_trigger.io.parquet import open_parquet_file


PHOTON_COLUMNS = tuple(f"L1T_PhotonTight_{field}" for field in ("PT", "Eta", "Phi"))


def collect_diphoton_mass_histogram(
    input_files: Sequence[str | Path],
    *,
    pair_mode: str = "all",
    pt_min_gev: float = 0.0,
    eta_max: float | None = None,
    bins: int = 100,
    mass_min_gev: float = 0.0,
    mass_max_gev: float = 200.0,
    collision_rate_mhz: float = 40.0,
    batch_size: int = 4096,
    progress: bool = False,
) -> dict[str, Any]:
    """Count pairs in bins and events in the inclusive [120,130] GeV window.

    All distinct pairs are used by default, matching the external mass-window
    trigger's pair policy. ``leading`` instead uses the two highest-pT selected
    photons. No isolation or photon-ID decision is added to the input collection.
    Histogram entries count pairs. Window efficiency/rate count each event once,
    including events without photons in the denominator. Results are unweighted.
    """
    if not input_files or pair_mode not in {"all", "leading"}:
        raise ValueError("Provide input_files and pair_mode='all' or 'leading'")
    if bins <= 0 or batch_size <= 0:
        raise ValueError("bins and batch_size must be positive")
    if not all(math.isfinite(x) for x in (pt_min_gev, mass_min_gev, mass_max_gev, collision_rate_mhz)):
        raise ValueError("pT, mass bounds, and collision rate must be finite")
    if pt_min_gev < 0 or not 0 <= mass_min_gev < mass_max_gev or collision_rate_mhz <= 0:
        raise ValueError("Require pT >= 0, 0 <= mass_min < mass_max, and collision rate > 0")
    if eta_max is not None and (not math.isfinite(eta_max) or eta_max <= 0):
        raise ValueError("eta_max must be finite and positive")
    # Resolve local symlinks too, so the same event file cannot be counted twice.
    sources = [str(path) if "://" in str(path) else str(Path(path).expanduser().resolve())
               for path in input_files]
    if len(set(sources)) != len(sources):
        raise ValueError("Duplicate input files would count the same events twice")
    edges = np.linspace(mass_min_gev, mass_max_gev, bins + 1)
    histogram = np.zeros(bins, dtype=np.int64)
    counters = ("events", "events_with_pairs", "pairs", "window_events", "window_pairs",
                "underflow_pairs", "overflow_pairs", "invalid_photons", "null_collection_events")
    totals = dict.fromkeys(counters, 0)
    per_file = []
    maximum_mass = None
    for file_index, source in enumerate(sources, 1):
        counts = dict.fromkeys(counters, 0)
        file_maximum = None
        with open_parquet_file(source) as parquet:
            missing = sorted(set(PHOTON_COLUMNS) - set(parquet.schema_arrow.names))
            if missing:
                raise ValueError(f"{source}: original event file is missing photon columns: {', '.join(missing)}")
            for batch in parquet.iter_batches(batch_size=batch_size, columns=list(PHOTON_COLUMNS)):
                arrays = [batch.column(batch.schema.get_field_index(name)).to_pylist() for name in PHOTON_COLUMNS]
                masses = []
                for pt_raw, eta_raw, phi_raw in zip(*arrays):
                    counts["events"] += 1
                    raw = (pt_raw, eta_raw, phi_raw)
                    values = [np.asarray([] if value is None else value, dtype=np.float64) for value in raw]
                    if any(value.ndim != 1 for value in values) or len({len(value) for value in values}) != 1:
                        raise ValueError(f"{source}, row {counts['events'] - 1}: photon PT/Eta/Phi must be aligned lists")
                    counts["null_collection_events"] += any(value is None for value in raw)
                    pt, eta, phi = values
                    finite = np.isfinite(pt) & np.isfinite(eta) & np.isfinite(phi) & (pt >= 0)
                    counts["invalid_photons"] += int(np.count_nonzero(~finite))
                    selected = finite & (pt >= pt_min_gev)
                    if eta_max is not None:
                        selected &= np.abs(eta) < eta_max
                    indices = np.flatnonzero(selected)
                    if len(indices) < 2:
                        continue
                    if pair_mode == "leading":
                        indices = indices[np.argsort(-pt[indices], kind="stable")[:2]]
                    counts["events_with_pairs"] += 1
                    in_window = False
                    for i, j in combinations(indices, 2):
                        try:
                            mass2 = 2 * float(pt[i]) * float(pt[j]) * (
                                math.cosh(float(eta[i] - eta[j])) - math.cos(float(phi[i] - phi[j]))
                            )
                            if not math.isfinite(mass2):
                                raise ValueError(f"Nonfinite mass squared in {source}, row {counts['events'] - 1}")
                            mass = math.sqrt(max(0.0, mass2))
                        except OverflowError as exc:
                            raise ValueError(f"Unphysical photon kinematics in {source}, row {counts['events'] - 1}") from exc
                        if not math.isfinite(mass):
                            raise ValueError(f"Nonfinite diphoton mass in {source}, row {counts['events'] - 1}")
                        masses.append(mass)
                        counts["pairs"] += 1
                        counts["underflow_pairs"] += mass < mass_min_gev
                        counts["overflow_pairs"] += mass > mass_max_gev
                        file_maximum = mass if file_maximum is None else max(file_maximum, mass)
                        if 120.0 <= mass <= 130.0:
                            counts["window_pairs"] += 1
                            in_window = True
                    counts["window_events"] += in_window
                histogram += np.histogram(masses, bins=edges)[0]
        for key in totals:
            totals[key] += counts[key]
        if file_maximum is not None:
            maximum_mass = file_maximum if maximum_mass is None else max(maximum_mass, file_maximum)
        per_file.append({"source": source, **counts, "maximum_mass_gev": file_maximum})
        if progress:
            print(f"[{file_index}/{len(sources)}] {source}: {counts['events']:,} events, "
                  f"{counts['pairs']:,} pairs, {counts['window_events']} window events", flush=True)
    if totals["events"] == 0:
        raise ValueError("Input files contain no events")
    if int(histogram.sum()) + totals["underflow_pairs"] + totals["overflow_pairs"] != totals["pairs"]:
        raise RuntimeError("Pair histogram accounting is inconsistent")
    efficiency = totals["window_events"] / totals["events"]
    return {
        "format": "diphoton-mass-histogram-v1", "collection": "L1T_PhotonTight",
        "required_columns": list(PHOTON_COLUMNS), "input_files": sources,
        "file_count": len(sources), "pair_mode": pair_mode,
        "photon_pt_min_gev": pt_min_gev, "photon_abs_eta_max": eta_max,
        "isolation_enabled": False, "weighting": "unweighted",
        "mass_definition": "Massless photons: m^2 = 2*pT1*pT2*(cosh(eta1-eta2)-cos(phi1-phi2))",
        "mass_window_gev": [120.0, 130.0], "mass_window_inclusive": True,
        "collision_rate_mhz": collision_rate_mhz, **totals,
        "window_event_efficiency": efficiency,
        "window_event_rate_khz": efficiency * collision_rate_mhz * 1000,
        "window_rate_definition": "Events with at least one selected pair in the window / all events * crossing rate",
        "maximum_mass_gev": maximum_mass,
        "histogram": {"bin_edges_gev": edges.tolist(), "pair_counts": histogram.tolist(),
                      "rightmost_edge_inclusive": True},
        "per_file": per_file,
    }


def plot_diphoton_mass(
    result: Mapping[str, Any], output_file: str | Path, *, dpi: int = 200,
    log_y: bool = True, overwrite: bool = False,
) -> Path:
    """Render counts; multiple entries per event are explicit in all-pairs mode."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output = Path(output_file).expanduser()
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output}; use --overwrite")
    if dpi <= 0:
        raise ValueError("dpi must be positive")
    edges = result["histogram"]["bin_edges_gev"]
    counts = result["histogram"]["pair_counts"]
    fig, ax = plt.subplots(figsize=(10, 6.5))
    ax.stairs(counts, edges, linewidth=1.7, color="#1769aa", label="Selected photon pairs")
    ax.axvspan(120, 130, color="#ef9b20", alpha=0.2, label="120–130 GeV window (inclusive)")
    ax.set_xlim(edges[0], edges[-1])
    positive = [value for value in counts if value > 0]
    if log_y and positive:
        ax.set_yscale("log")
        ax.set_ylim(0.7, max(positive) * 4)
    else:
        ax.set_ylim(0, max(positive, default=1) * 1.4)
    ax.set_xlabel(r"Diphoton invariant mass $m_{\gamma\gamma}$ [GeV]", fontsize=12)
    ax.set_ylabel("Photon pairs / bin (unweighted)", fontsize=12)
    ax.set_title(f"Minimum-bias diphoton invariant mass — {result['file_count']} files")
    mode = "All distinct pairs; events can contribute multiple entries" if result["pair_mode"] == "all" else "Two highest-pT selected photons per event"
    eta = result["photon_abs_eta_max"]
    eta_label = "no eta cut" if eta is None else f"|eta| < {eta:g}"
    lines = [f"Events: {result['events']:,}; pairs: {result['pairs']:,}",
             f"PhotonTight pT >= {result['photon_pt_min_gev']:g} GeV; {eta_label}",
             "No additional isolation cut",
             f"120–130 GeV: {result['window_pairs']} pairs in {result['window_events']} events",
             f"Window event efficiency: {100 * result['window_event_efficiency']:.5g}%",
             f"Window event rate at {result['collision_rate_mhz']:g} MHz: {result['window_event_rate_khz']:.5g} kHz",
             f"Pairs outside plotted range: {result['underflow_pairs'] + result['overflow_pairs']}"]
    ax.text(0.98, 0.97, "\n".join(lines), transform=ax.transAxes, ha="right", va="top", fontsize=9,
            bbox={"boxstyle": "round,pad=0.4", "facecolor": "white", "edgecolor": "#aaaaaa", "alpha": 0.9})
    if not positive:
        ax.text(0.5, 0.4, "No pairs in the displayed mass range", transform=ax.transAxes, ha="center")
    ax.grid(True, alpha=0.25, which="both")
    ax.legend(loc="lower left", fontsize=9)
    fig.text(0.5, 0.015, mode, ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.savefig(output, dpi=dpi)
    finally:
        plt.close(fig)
    return output
