#!/usr/bin/env python3
"""Plot leading-object distributions and empirical rate thresholds.

Each selected Parquet column may either be a scalar or a per-event list.  For a
list column, the plotted value is the largest finite value in the event.  Empty
collections are omitted from the histogram but remain in the denominator used
to convert an efficiency into a trigger rate.

Rates supplied with ``--rate`` are interpreted as kHz unless a Hz, kHz, or MHz
suffix is present.  For a requested rate R and collision rate C, the script
chooses the order-statistic threshold corresponding to round(N * R / C)
passing events.  The achieved rate is reported because a finite sample and
ties can prevent an exact match.

Example using the nominal menu rates and object eta acceptances::

    python plot_leading_object_distributions.py \
      'data/minbias/minbias-NEVENT10000-RS590000??.parquet' \
      --column L1T_JetPuppiAK4_PT \
      --column L1T_MuonTight_PT \
      --column L1T_Electron_PT \
      --column L1T_PUPPIMET_MET \
      --eta-cut L1T_JetPuppiAK4_PT=L1T_JetPuppiAK4_Eta:2.4 \
      --eta-cut L1T_MuonTight_PT=L1T_MuonTight_Eta:2.4 \
      --eta-cut L1T_Electron_PT=L1T_Electron_Eta:2.4 \
      --rate L1T_JetPuppiAK4_PT=37 \
      --rate L1T_MuonTight_PT=17 \
      --rate L1T_Electron_PT=28 \
      --rate L1T_PUPPIMET_MET=30 \
      --output leading_objects.png \
      --report leading_objects.txt
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import glob
import math
from pathlib import Path
import re
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


DEFAULT_COLUMNS = (
    "L1T_JetPuppiAK4_PT",
    "L1T_MuonTight_PT",
    "L1T_Electron_PT",
    "L1T_PUPPIMET_MET",
)

DISPLAY_NAMES = {
    "L1T_JetPuppiAK4_PT": r"Leading PUPPI AK4 jet $p_T$",
    "L1T_MuonTight_PT": r"Leading MuonTight $p_T$",
    "L1T_Electron_PT": r"Leading electron $p_T$",
    "L1T_PhotonTight_PT": r"Leading PhotonTight $p_T$",
    "L1T_PUPPIMET_MET": r"PUPPI $p_T^{miss}$",
    "L1T_MET_MET": r"PF $p_T^{miss}$",
    "L1T_ScalarHT_HT": r"Scalar $H_T$",
}

RATE_PATTERN = re.compile(
    r"^\s*([+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*"
    r"(Hz|kHz|MHz)?\s*$",
    re.IGNORECASE,
)

CLI_EXAMPLE = (
    "Example with menu rates and object eta acceptances:\n\n"
    "  python plot_leading_object_distributions.py \\\n"
    "    'data/minbias/minbias-NEVENT10000-RS590000??.parquet' \\\n"
    "    --column L1T_JetPuppiAK4_PT \\\n"
    "    --column L1T_MuonTight_PT \\\n"
    "    --column L1T_Electron_PT \\\n"
    "    --column L1T_PUPPIMET_MET \\\n"
    "    --eta-cut L1T_JetPuppiAK4_PT=L1T_JetPuppiAK4_Eta:2.4 \\\n"
    "    --eta-cut L1T_MuonTight_PT=L1T_MuonTight_Eta:2.4 \\\n"
    "    --eta-cut L1T_Electron_PT=L1T_Electron_Eta:2.4 \\\n"
    "    --rate L1T_JetPuppiAK4_PT=37 \\\n"
    "    --rate L1T_MuonTight_PT=17 \\\n"
    "    --rate L1T_Electron_PT=28 \\\n"
    "    --rate L1T_PUPPIMET_MET=30 \\\n"
    "    --output leading_objects.png \\\n"
    "    --report leading_objects.txt"
)


@dataclass(frozen=True)
class EtaCut:
    eta_column: str
    max_abs_eta: float


@dataclass(frozen=True)
class ThresholdResult:
    requested_rate_khz: float
    target_events: float
    rounded_target_events: int
    threshold: float | None
    passing_events: int
    achieved_rate_khz: float
    maximum_rate_khz: float
    attainable: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot leading values from scalar or list-valued Parquet columns and "
            "mark thresholds corresponding to requested trigger rates."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CLI_EXAMPLE,
    )
    parser.add_argument(
        "input",
        nargs="+",
        help="Input Parquet files or glob patterns",
    )
    parser.add_argument(
        "--column",
        action="append",
        dest="columns",
        metavar="NAME",
        help=(
            "Column to plot; repeat for multiple columns. Defaults to "
            + ", ".join(DEFAULT_COLUMNS)
        ),
    )
    parser.add_argument(
        "--rate",
        action="append",
        default=[],
        metavar="COLUMN=RATE",
        help=(
            "Requested rate for a column; repeat as needed. A bare value is in "
            "kHz; Hz, kHz, and MHz suffixes are accepted. Multiple rates may be "
            "given for the same column."
        ),
    )
    parser.add_argument(
        "--eta-cut",
        action="append",
        default=[],
        metavar="VALUE_COLUMN=ETA_COLUMN:MAX",
        help=(
            "Restrict a list-valued object column to |eta| < MAX before finding "
            "the leading value; repeat for multiple object columns."
        ),
    )
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        metavar="COLUMN=TEXT",
        help="Override a subplot label; repeat for multiple columns",
    )
    parser.add_argument(
        "--x-max",
        action="append",
        default=[],
        metavar="COLUMN=VALUE",
        help="Override the upper x-axis limit for a column",
    )
    parser.add_argument(
        "--collision-rate-mhz",
        type=float,
        default=40.0,
        help="Bunch-crossing rate in MHz (default: 40)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=65536,
        help="Events read per Parquet batch (default: 65536)",
    )
    parser.add_argument(
        "--bins",
        type=int,
        default=80,
        help="Histogram bins per subplot (default: 80)",
    )
    parser.add_argument(
        "--linear-y",
        action="store_true",
        help="Use a linear histogram y-axis instead of logarithmic",
    )
    parser.add_argument(
        "--title",
        default="Leading L1-object distributions and rate thresholds",
        help="Figure title",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "results/leading_object_distributions.png",
        help="Output image (default: results/leading_object_distributions.png)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional text report containing the calculated thresholds",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Output resolution (default: 200 DPI)",
    )
    return parser.parse_args()


def resolve_inputs(inputs: Sequence[str]) -> list[Path]:
    """Expand input patterns and reject duplicates to prevent double counting."""
    paths: list[Path] = []
    seen: set[Path] = set()
    for value in inputs:
        expanded = str(Path(value).expanduser())
        matches = [expanded] if Path(expanded).is_file() else sorted(glob.glob(expanded))
        if not matches:
            raise FileNotFoundError(f"No input files found: {value}")
        for match in matches:
            path = Path(match)
            if not path.is_file():
                raise FileNotFoundError(f"Input is not a file: {path}")
            if path.suffix.lower() != ".parquet":
                raise ValueError(f"Input must be a .parquet file: {path}")
            resolved = path.resolve()
            if resolved in seen:
                raise ValueError(f"Input file specified more than once: {path}")
            seen.add(resolved)
            paths.append(path)
    return paths


def parse_column_mapping(
    specifications: Sequence[str], option_name: str
) -> dict[str, str]:
    result: dict[str, str] = {}
    for specification in specifications:
        if "=" not in specification:
            raise ValueError(
                f"{option_name} must have the form COLUMN=VALUE: {specification}"
            )
        column, value = specification.split("=", 1)
        column, value = column.strip(), value.strip()
        if not column or not value:
            raise ValueError(f"Malformed {option_name}: {specification}")
        if column in result:
            raise ValueError(f"{option_name} was specified twice for {column}")
        result[column] = value
    return result


def parse_eta_cuts(specifications: Sequence[str]) -> dict[str, EtaCut]:
    raw = parse_column_mapping(specifications, "--eta-cut")
    result: dict[str, EtaCut] = {}
    for value_column, definition in raw.items():
        if ":" not in definition:
            raise ValueError(
                "--eta-cut must have the form "
                f"VALUE_COLUMN=ETA_COLUMN:MAX: {value_column}={definition}"
            )
        eta_column, maximum = definition.rsplit(":", 1)
        eta_column = eta_column.strip()
        try:
            max_abs_eta = float(maximum)
        except ValueError as exc:
            raise ValueError(f"Invalid eta limit in --eta-cut: {definition}") from exc
        if not eta_column or not np.isfinite(max_abs_eta) or max_abs_eta <= 0:
            raise ValueError(f"Invalid --eta-cut: {value_column}={definition}")
        result[value_column] = EtaCut(eta_column, max_abs_eta)
    return result


def rate_to_khz(value: str) -> float:
    match = RATE_PATTERN.fullmatch(value)
    if not match:
        raise ValueError(f"Invalid rate '{value}'; examples: 37, 37kHz, 0.037MHz")
    number = float(match.group(1))
    unit = (match.group(2) or "kHz").lower()
    scale = {"hz": 0.001, "khz": 1.0, "mhz": 1000.0}[unit]
    rate = number * scale
    if not np.isfinite(rate) or rate < 0:
        raise ValueError(f"Rate must be finite and nonnegative: {value}")
    return rate


def parse_rates(
    specifications: Sequence[str], selected_columns: Sequence[str]
) -> dict[str, list[float]]:
    selected = set(selected_columns)
    result: dict[str, list[float]] = defaultdict(list)
    for specification in specifications:
        if "=" not in specification:
            raise ValueError(
                f"--rate must have the form COLUMN=RATE: {specification}"
            )
        column, value = specification.split("=", 1)
        column = column.strip()
        if column not in selected:
            raise ValueError(
                f"--rate refers to unselected column '{column}'. "
                "Add it with --column."
            )
        for item in value.split(","):
            result[column].append(rate_to_khz(item))
    return dict(result)


def parse_x_max(specifications: Sequence[str]) -> dict[str, float]:
    raw = parse_column_mapping(specifications, "--x-max")
    result: dict[str, float] = {}
    for column, value in raw.items():
        try:
            maximum = float(value)
        except ValueError as exc:
            raise ValueError(f"Invalid --x-max value for {column}: {value}") from exc
        if not np.isfinite(maximum):
            raise ValueError(f"--x-max must be finite for {column}")
        result[column] = maximum
    return result


def validate_selected_keys(
    mapping: dict[str, object], selected_columns: Sequence[str], option_name: str
) -> None:
    extra = sorted(set(mapping) - set(selected_columns))
    if extra:
        raise ValueError(
            f"{option_name} refers to unselected column(s): {', '.join(extra)}"
        )


def numeric_type(data_type: pa.DataType) -> bool:
    if pa.types.is_list(data_type) or pa.types.is_large_list(data_type):
        return numeric_type(data_type.value_type)
    if pa.types.is_fixed_size_list(data_type):
        return numeric_type(data_type.value_type)
    return (
        pa.types.is_integer(data_type)
        or pa.types.is_floating(data_type)
        or pa.types.is_decimal(data_type)
    )


def validate_schema(
    parquet_file: pq.ParquetFile,
    required_columns: Sequence[str],
    input_path: Path,
) -> None:
    schema = parquet_file.schema_arrow
    missing = [name for name in required_columns if name not in schema.names]
    if missing:
        raise ValueError(
            f"Input {input_path} is missing required columns: {', '.join(missing)}"
        )
    for name in required_columns:
        if not numeric_type(schema.field(name).type):
            raise TypeError(
                f"Column {name} in {input_path} is not scalar or list-valued numeric data: "
                f"{schema.field(name).type}"
            )


def as_one_dimensional_float(value: object) -> np.ndarray:
    if value is None:
        return np.asarray([], dtype=float)
    if isinstance(value, (list, tuple, np.ndarray)):
        return np.asarray(value, dtype=float).reshape(-1)
    return np.asarray([value], dtype=float)


def leading_values(
    value_rows: Sequence[object],
    eta_rows: Sequence[object] | None = None,
    max_abs_eta: float | None = None,
    *,
    column: str,
    first_event_index: int,
) -> np.ndarray:
    """Return one leading value per event, using NaN for an empty selection."""
    output = np.full(len(value_rows), np.nan, dtype=float)
    if eta_rows is not None and len(eta_rows) != len(value_rows):
        raise ValueError(f"Batch length mismatch between {column} and its eta column")

    for local_index, raw_values in enumerate(value_rows):
        values = as_one_dimensional_float(raw_values)
        mask = np.isfinite(values)
        if eta_rows is not None:
            etas = as_one_dimensional_float(eta_rows[local_index])
            if len(etas) != len(values):
                event_index = first_event_index + local_index
                raise ValueError(
                    f"Object-count mismatch for {column} at combined event "
                    f"{event_index}: {len(values)} values versus {len(etas)} eta values"
                )
            mask &= np.isfinite(etas) & (np.abs(etas) < float(max_abs_eta))
        selected = values[mask]
        if len(selected):
            output[local_index] = float(np.max(selected))
    return output


def read_leading_distributions(
    input_paths: Sequence[Path],
    columns: Sequence[str],
    eta_cuts: dict[str, EtaCut],
    batch_size: int,
) -> tuple[dict[str, np.ndarray], int]:
    required = list(columns)
    for cut in eta_cuts.values():
        if cut.eta_column not in required:
            required.append(cut.eta_column)

    pieces: dict[str, list[np.ndarray]] = {name: [] for name in columns}
    total_events = 0
    for input_path in input_paths:
        with pq.ParquetFile(input_path) as parquet_file:
            validate_schema(parquet_file, required, input_path)
            file_events = 0
            for batch in parquet_file.iter_batches(
                columns=required, batch_size=batch_size
            ):
                rows = {
                    name: batch.column(batch.schema.get_field_index(name)).to_pylist()
                    for name in required
                }
                for column in columns:
                    cut = eta_cuts.get(column)
                    pieces[column].append(
                        leading_values(
                            rows[column],
                            rows[cut.eta_column] if cut else None,
                            cut.max_abs_eta if cut else None,
                            column=column,
                            first_event_index=total_events,
                        )
                    )
                total_events += batch.num_rows
                file_events += batch.num_rows
        print(f"Read {file_events} events from {input_path}", flush=True)

    return (
        {
            name: np.concatenate(arrays) if arrays else np.asarray([], dtype=float)
            for name, arrays in pieces.items()
        },
        total_events,
    )


def threshold_for_rate(
    values: np.ndarray,
    requested_rate_khz: float,
    collision_rate_khz: float,
) -> ThresholdResult:
    total_events = len(values)
    if total_events == 0:
        raise ValueError("Cannot calculate a rate threshold from zero events")
    if requested_rate_khz > collision_rate_khz:
        raise ValueError(
            f"Requested rate {requested_rate_khz:g} kHz exceeds the collision "
            f"rate {collision_rate_khz:g} kHz"
        )

    finite = values[np.isfinite(values)]
    target = total_events * requested_rate_khz / collision_rate_khz
    # Use conventional round-half-up rather than Python's round-to-even so a
    # target of, for example, 18.5 events maps deterministically to 19.
    rounded_target = int(math.floor(target + 0.5))
    maximum_rate = len(finite) / total_events * collision_rate_khz

    if not len(finite):
        return ThresholdResult(
            requested_rate_khz,
            target,
            rounded_target,
            None,
            0,
            0.0,
            0.0,
            False,
        )

    if rounded_target <= 0:
        threshold = float(np.nextafter(np.max(finite), math.inf))
        passing = 0
    elif rounded_target > len(finite):
        threshold = float(np.min(finite))
        passing = len(finite)
    else:
        # np.partition avoids sorting the complete distribution.  Ties at the
        # selected value are all accepted by the >= threshold convention.
        rank_from_smallest = len(finite) - rounded_target
        threshold = float(np.partition(finite, rank_from_smallest)[rank_from_smallest])
        passing = int(np.count_nonzero(finite >= threshold))

    achieved = passing / total_events * collision_rate_khz
    return ThresholdResult(
        requested_rate_khz,
        target,
        rounded_target,
        threshold,
        passing,
        achieved,
        maximum_rate,
        requested_rate_khz <= maximum_rate,
    )


def default_display_name(column: str) -> str:
    if column in DISPLAY_NAMES:
        return DISPLAY_NAMES[column]
    name = column.removeprefix("L1T_").replace("_", " ")
    return f"Leading {name}"


def format_threshold(value: float | None) -> str:
    if value is None:
        return "unavailable"
    magnitude = abs(value)
    if magnitude >= 100:
        return f"{value:.1f}"
    if magnitude >= 10:
        return f"{value:.2f}"
    return f"{value:.3f}"


def plot_distributions(
    distributions: dict[str, np.ndarray],
    columns: Sequence[str],
    rates: dict[str, list[float]],
    eta_cuts: dict[str, EtaCut],
    labels: dict[str, str],
    x_maxima: dict[str, float],
    collision_rate_khz: float,
    bins: int,
    linear_y: bool,
    title: str,
    output_path: Path,
    dpi: int,
) -> dict[str, list[ThresholdResult]]:
    count = len(columns)
    grid_columns = 1 if count == 1 else 2
    grid_rows = math.ceil(count / grid_columns)
    fig, axes = plt.subplots(
        grid_rows,
        grid_columns,
        figsize=(7.1 * grid_columns, 4.8 * grid_rows),
        squeeze=False,
    )
    colors = plt.get_cmap("tab10").colors
    results: dict[str, list[ThresholdResult]] = {}

    for plot_index, column in enumerate(columns):
        ax = axes.flat[plot_index]
        values = distributions[column]
        finite = values[np.isfinite(values)]
        if not len(finite):
            raise ValueError(f"No finite selected values were found for {column}")

        lower = min(0.0, float(np.min(finite)))
        upper = x_maxima.get(column, float(np.max(finite)) * 1.02)
        if upper <= lower:
            upper = lower + 1.0
        edges = np.linspace(lower, upper, bins + 1)
        ax.hist(
            finite,
            bins=edges,
            histtype="stepfilled",
            color="#4c78a8",
            edgecolor="#2f5f8f",
            alpha=0.42,
            linewidth=1.25,
        )

        column_results = [
            threshold_for_rate(values, rate, collision_rate_khz)
            for rate in rates.get(column, [])
        ]
        results[column] = column_results
        for rate_index, result in enumerate(column_results):
            if result.threshold is None:
                continue
            threshold_text = format_threshold(result.threshold)
            label = f"{result.requested_rate_khz:g} kHz → {threshold_text} GeV"
            rate_step = collision_rate_khz / len(values)
            if abs(result.achieved_rate_khz - result.requested_rate_khz) > 0.51 * rate_step:
                label += f" (emp. {result.achieved_rate_khz:g} kHz)"
            if not result.attainable:
                label += " (above max rate)"
            ax.axvline(
                result.threshold,
                color=colors[rate_index % len(colors)],
                linestyle="--",
                linewidth=1.6,
                label=label,
            )

        display_name = labels.get(column, default_display_name(column))
        cut = eta_cuts.get(column)
        if cut:
            display_name += rf", $|\eta| < {cut.max_abs_eta:g}$"
        nonempty = len(finite)
        ax.set_title(display_name)
        ax.set_xlabel("Leading value [GeV]")
        ax.set_ylabel("Events / bin")
        ax.set_xlim(lower, upper)
        if not linear_y:
            ax.set_yscale("log")
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        ax.set_axisbelow(True)
        ax.text(
            0.98,
            0.96,
            f"nonempty: {nonempty:,}/{len(values):,}",
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=8,
            color="0.35",
        )
        if column_results:
            ax.legend(loc="best", frameon=False, fontsize=11)

    for unused_index in range(count, grid_rows * grid_columns):
        axes.flat[unused_index].set_visible(False)

    fig.suptitle(title, y=1.01, fontsize=14)
    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return results


def report_text(
    input_paths: Sequence[Path],
    columns: Sequence[str],
    distributions: dict[str, np.ndarray],
    results: dict[str, list[ThresholdResult]],
    eta_cuts: dict[str, EtaCut],
    collision_rate_khz: float,
) -> str:
    total_events = len(next(iter(distributions.values())))
    lines = [
        "Leading-object empirical rate thresholds",
        *(f"Input: {path}" for path in input_paths),
        f"Events: {total_events}",
        f"Collision rate: {collision_rate_khz / 1000.0:g} MHz",
        f"Single-event rate resolution: {collision_rate_khz / total_events:g} kHz",
        "Threshold convention: leading value >= threshold",
        "Empty object collections remain in the event denominator.",
        "",
    ]

    for column in columns:
        values = distributions[column]
        nonempty = int(np.count_nonzero(np.isfinite(values)))
        lines.append(f"Column: {column}")
        if column in eta_cuts:
            cut = eta_cuts[column]
            lines.append(f"  Selection: |{cut.eta_column}| < {cut.max_abs_eta:g}")
        lines.append(f"  Nonempty events: {nonempty}/{len(values)}")
        if not results[column]:
            lines.append("  No rates requested")
        for result in results[column]:
            lines.extend(
                [
                    f"  Requested rate: {result.requested_rate_khz:g} kHz",
                    f"    Target events: {result.target_events:.6g} "
                    f"(rounded to {result.rounded_target_events})",
                    f"    Threshold: {format_threshold(result.threshold)} GeV",
                    f"    Passing events: {result.passing_events}/{len(values)}",
                    f"    Achieved rate: {result.achieved_rate_khz:.6g} kHz",
                    f"    Maximum rate from nonempty events: "
                    f"{result.maximum_rate_khz:.6g} kHz",
                    f"    Requested rate attainable: {'yes' if result.attainable else 'no'}",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def validate_output_path(path: Path, description: str) -> None:
    if path.exists():
        raise FileExistsError(
            f"{description} already exists: {path}\n"
            "Choose another path or remove the existing file explicitly."
        )


def main() -> None:
    args = parse_args()
    input_paths = resolve_inputs(args.input)
    columns = args.columns or list(DEFAULT_COLUMNS)
    if not columns:
        raise ValueError("At least one --column is required")
    if len(columns) != len(set(columns)):
        raise ValueError("The same --column was specified more than once")
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be positive")
    if args.bins <= 0:
        raise ValueError("--bins must be positive")
    if args.dpi <= 0:
        raise ValueError("--dpi must be positive")
    if not np.isfinite(args.collision_rate_mhz) or args.collision_rate_mhz <= 0:
        raise ValueError("--collision-rate-mhz must be finite and positive")

    eta_cuts = parse_eta_cuts(args.eta_cut)
    labels = parse_column_mapping(args.label, "--label")
    x_maxima = parse_x_max(args.x_max)
    validate_selected_keys(eta_cuts, columns, "--eta-cut")
    validate_selected_keys(labels, columns, "--label")
    validate_selected_keys(x_maxima, columns, "--x-max")
    rates = parse_rates(args.rate, columns)

    collision_rate_khz = args.collision_rate_mhz * 1000.0
    for column, requested_rates in rates.items():
        for rate in requested_rates:
            if rate > collision_rate_khz:
                raise ValueError(
                    f"Requested rate for {column} ({rate:g} kHz) exceeds the "
                    f"collision rate ({collision_rate_khz:g} kHz)"
                )

    output_path = args.output.expanduser()
    report_path = args.report.expanduser() if args.report else None
    validate_output_path(output_path, "Output image")
    if report_path:
        validate_output_path(report_path, "Output report")
        if report_path.resolve() == output_path.resolve():
            raise ValueError("--report must differ from --output")

    distributions, total_events = read_leading_distributions(
        input_paths, columns, eta_cuts, args.batch_size
    )
    if total_events == 0:
        raise ValueError("The input files contain no events")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    results = plot_distributions(
        distributions,
        columns,
        rates,
        eta_cuts,
        labels,
        x_maxima,
        collision_rate_khz,
        args.bins,
        args.linear_y,
        args.title,
        output_path,
        args.dpi,
    )
    report = report_text(
        input_paths,
        columns,
        distributions,
        results,
        eta_cuts,
        collision_rate_khz,
    )
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
        print(f"Wrote threshold report to {report_path}")
    else:
        print(report, end="")
    print(f"Wrote plot to {output_path}")


if __name__ == "__main__":
    main()
