#!/usr/bin/env python3
"""Plot trigger efficiencies from calculate_trigger_efficiencies.py output."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot trigger efficiencies in decreasing order."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Text report produced by calculate_trigger_efficiencies.py",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output image (default: <input stem>.png)",
    )
    parser.add_argument(
        "--exclude-overall",
        action="store_true",
        help="Do not plot the passes_any summary row",
    )
    parser.add_argument(
        "--title",
        default="COLLIDE-2V Trigger Efficiencies",
        help="Plot title",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Output resolution (default: 200 DPI)",
    )
    return parser.parse_args()


def read_efficiencies(path: Path) -> list[tuple[str, float]]:
    """Return (trigger name, efficiency fraction) rows from the text report."""
    rows: list[tuple[str, float]] = []
    in_table = False

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("Trigger") and "Efficiency" in stripped:
            in_table = True
            continue
        if not in_table or not stripped or set(stripped) == {"-"}:
            continue

        fields = stripped.split()
        if len(fields) != 6:
            raise ValueError(f"Malformed table row at line {line_number}: {line}")
        name, _passed, _total, efficiency, _percent, _nulls = fields
        try:
            value = float(efficiency)
        except ValueError as exc:
            raise ValueError(
                f"Invalid efficiency at line {line_number}: {efficiency}"
            ) from exc
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"Efficiency outside [0, 1] at line {line_number}: {value}"
            )
        rows.append((name, value))

    if not rows:
        raise ValueError(
            "No efficiency rows found. Expected output from "
            "calculate_trigger_efficiencies.py."
        )
    return rows


def plot_efficiencies(
    rows: list[tuple[str, float]],
    output_path: Path,
    title: str,
    dpi: int,
) -> None:
    # Python's sort is stable, so equal-efficiency triggers retain report order.
    rows = sorted(rows, key=lambda item: item[1], reverse=True)
    names = [name for name, _ in rows]
    percentages = [100.0 * efficiency for _, efficiency in rows]

    figure_width = max(12.0, 0.43 * len(rows))
    fig, ax = plt.subplots(figsize=(figure_width, 7.5))
    bars = ax.bar(range(len(rows)), percentages, color="#3677b5", width=0.82)

    ax.set_title(title, pad=14)
    ax.set_xlabel("Trigger (decreasing efficiency)")
    ax.set_ylabel("Efficiency [%]")
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels(names, rotation=65, ha="right", fontsize=8)
    ax.set_ylim(0, max(1.0, max(percentages) * 1.12))
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)

    for bar, percentage in zip(bars, percentages):
        ax.annotate(
            f"{percentage:.2f}%",
            xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            rotation=90,
            fontsize=7,
        )

    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    input_path = args.input.expanduser()
    output_path = (
        args.output.expanduser()
        if args.output
        else input_path.with_suffix(".png")
    )

    if not input_path.is_file():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")
    if input_path.suffix.lower() != ".txt":
        raise ValueError(f"Input must be a .txt file: {input_path}")
    if output_path.resolve() == input_path.resolve():
        raise ValueError("Output image must differ from the input text file.")
    if output_path.exists():
        raise FileExistsError(
            f"Output already exists: {output_path}\n"
            "Choose another path or remove the existing image explicitly."
        )
    if args.dpi <= 0:
        raise ValueError("--dpi must be positive")

    rows = read_efficiencies(input_path)
    if args.exclude_overall:
        rows = [(name, value) for name, value in rows if name != "passes_any"]
        if not rows:
            raise ValueError("No individual trigger rows remain after excluding passes_any.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plot_efficiencies(rows, output_path, args.title, args.dpi)
    print(f"Plotted {len(rows)} efficiencies in decreasing order.")
    print(f"Wrote plot to {output_path}")


if __name__ == "__main__":
    main()
