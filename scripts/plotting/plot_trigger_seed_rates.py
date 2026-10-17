#!/usr/bin/env python3
"""Plot reference seed rates and equivalent efficiencies from menu metadata."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from collide2v_trigger import IDBasedTriggerMenu


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "results/trigger_seed_rates.png",
    )
    parser.add_argument("--collision-rate-mhz", type=float, default=40.0)
    parser.add_argument("--implementable-only", action="store_true",
                        help="Show only the 33 seeds evaluated by the apply scripts")
    parser.add_argument("--dpi", type=int, default=200)
    args = parser.parse_args()
    if not math.isfinite(args.collision_rate_mhz) or args.collision_rate_mhz <= 0:
        parser.error("--collision-rate-mhz must be finite and positive")
    if args.dpi <= 0:
        parser.error("--dpi must be positive")
    output = args.output.expanduser()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}; choose another path.")

    menu = IDBasedTriggerMenu()
    names = menu.implementable_triggers if args.implementable_only else menu.all_triggers
    names = sorted(names, key=lambda name: (-menu.trigger_info[name].rate_khz, name))
    rates = [menu.trigger_info[name].rate_khz for name in names]
    collision_rate_khz = args.collision_rate_mhz * 1000.0
    percentages = [100.0 * rate / collision_rate_khz for rate in rates]

    fig, ax = plt.subplots(figsize=(max(12.0, 0.43 * len(names)), 7.5))
    bars = ax.bar(range(len(names)), percentages, color="#3677b5", width=0.82)
    ax.set_title(
        "COLLIDE-2V Reference Trigger Seed Rates\n"
        f"Equivalent efficiency at {args.collision_rate_mhz:g} MHz collision rate",
        pad=14,
    )
    ax.set_xlabel("Trigger (decreasing reference rate)")
    ax.set_ylabel("Equivalent efficiency [%]")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=65, ha="right", fontsize=8)
    ax.set_ylim(0, max(percentages) * 1.4 if max(percentages) else 1.0)
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)
    rate_axis = ax.secondary_yaxis(
        "right",
        functions=(lambda percent: percent * collision_rate_khz / 100.0,
                   lambda rate: rate * 100.0 / collision_rate_khz),
    )
    rate_axis.set_ylabel("Reference rate [kHz]")
    for bar, rate, percentage in zip(bars, rates, percentages):
        ax.annotate(
            f"{rate:g} kHz / {percentage:.4f}%",
            xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
            xytext=(0, 3), textcoords="offset points",
            ha="center", va="bottom", rotation=90, fontsize=7,
        )
    fig.text(0.5, 0.01,
             "Rates are menu metadata; efficiencies = rate / collision rate. "
             "Overlapping seed rates must not be summed to obtain the menu OR rate.",
             ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.035, 1, 1))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=args.dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"Plotted {len(names)} seed rates at {args.collision_rate_mhz:g} MHz: {output}")


if __name__ == "__main__":
    main()
