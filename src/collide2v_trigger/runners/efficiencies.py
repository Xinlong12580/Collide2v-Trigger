#!/usr/bin/env python3
"""Calculate trigger efficiencies from one or more trigger-bit Parquet files.

This runner expects output produced by ``collide2v-apply-triggers`` or
``collide2v-apply-id-triggers``.
Efficiency is defined as the unweighted fraction of all input events whose
Boolean trigger bit is true. Null trigger values, if present, are treated as
false and reported in the output. Counts are summed across all files before
dividing by the total event count. All files must have matching Boolean trigger
columns. Events duplicated across different files are counted separately.
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


TRIGGER_PREFIX = "Trigger_"
OVERALL_COLUMN = "Trigger_passes_any"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate unweighted efficiencies from saved trigger bits."
    )
    parser.add_argument(
        "input",
        nargs="+",
        help="Trigger-bit Parquet files or glob patterns (from either apply script)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output path (default: <input stem>_efficiencies.txt for one file; "
             "combined_trigger_efficiencies.txt beside the first input for multiple files)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=65536,
        help="Rows processed per batch (default: 65536)",
    )
    return parser.parse_args()


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_efficiencies.txt")


def resolve_inputs(inputs: list[str]) -> list[Path]:
    """Expand patterns and reject repeated paths to prevent double counting."""
    paths = []
    seen = set()
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
            if path.resolve() in seen:
                raise ValueError(f"Input file specified more than once: {path}")
            seen.add(path.resolve())
            paths.append(path)
    return paths


def trigger_columns(parquet_file: pq.ParquetFile) -> list[str]:
    columns = [
        name
        for name in parquet_file.schema_arrow.names
        if name.startswith(TRIGGER_PREFIX)
    ]
    if not columns:
        raise ValueError(
            f"No columns beginning with '{TRIGGER_PREFIX}' were found. "
            "Use a file produced by one of the trigger application runners."
        )

    if len(columns) != len(set(columns)):
        raise ValueError("Input contains duplicate trigger column names")
    for name in columns:
        if not pa.types.is_boolean(parquet_file.schema_arrow.field(name).type):
            raise ValueError(f"Trigger column must be Boolean: {name}")

    # Put the overall OR decision after the individual trigger decisions.
    individual = sorted(name for name in columns if name != OVERALL_COLUMN)
    return individual + ([OVERALL_COLUMN] if OVERALL_COLUMN in columns else [])


def calculate_counts(
    parquet_file: pq.ParquetFile,
    columns: list[str],
    batch_size: int,
) -> tuple[int, dict[str, int], dict[str, int]]:
    passed = {name: 0 for name in columns}
    nulls = {name: 0 for name in columns}
    total = 0

    for batch in parquet_file.iter_batches(columns=columns, batch_size=batch_size):
        total += batch.num_rows
        for index, name in enumerate(columns):
            values = batch.column(index)
            passed[name] += int(pc.sum(pc.fill_null(values, False)).as_py() or 0)
            nulls[name] += values.null_count

    return total, passed, nulls


def write_report(
    output_path: Path,
    input_paths: list[Path],
    columns: list[str],
    total: int,
    passed: dict[str, int],
    nulls: dict[str, int],
) -> None:
    name_width = max(len("Trigger"), *(len(name.removeprefix(TRIGGER_PREFIX)) for name in columns))
    header = (
        f"{'Trigger':<{name_width}}  {'Passed':>10}  "
        f"{'Total':>10}  {'Efficiency':>12}  {'Percent':>10}  {'Nulls':>10}"
    )
    lines = [
        "COLLIDE-2V trigger efficiencies",
        *(f"Input: {path}" for path in input_paths),
        "Definition: efficiency = passed events / all events (unweighted)",
        "Null trigger values are counted as failures.",
        "",
        header,
        "-" * len(header),
    ]

    for column in columns:
        display_name = column.removeprefix(TRIGGER_PREFIX)
        efficiency = passed[column] / total if total else 0.0
        lines.append(
            f"{display_name:<{name_width}}  {passed[column]:>10d}  "
            f"{total:>10d}  {efficiency:>12.8f}  "
            f"{100.0 * efficiency:>9.4f}%  {nulls[column]:>10d}"
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    input_paths = resolve_inputs(args.input)
    output_path = args.output.expanduser() if args.output else (
        default_output_path(input_paths[0]) if len(input_paths) == 1
        else input_paths[0].with_name("combined_trigger_efficiencies.txt")
    )

    if args.batch_size <= 0:
        raise ValueError("--batch-size must be positive")
    if output_path.exists():
        raise FileExistsError(
            f"Output already exists: {output_path}\n"
            "Choose another path or remove the existing report explicitly."
        )

    columns = None
    total = 0
    passed: dict[str, int] = {}
    nulls: dict[str, int] = {}
    for input_path in input_paths:
        with pq.ParquetFile(input_path) as parquet_file:
            file_columns = trigger_columns(parquet_file)
            if columns is None:
                columns = file_columns
                passed = dict.fromkeys(columns, 0)
                nulls = dict.fromkeys(columns, 0)
            elif file_columns != columns:
                missing = sorted(set(columns) - set(file_columns))
                extra = sorted(set(file_columns) - set(columns))
                raise ValueError(
                    f"Trigger columns differ in {input_path}: missing={missing}, extra={extra}"
                )
            count, file_passed, file_nulls = calculate_counts(
                parquet_file, columns, args.batch_size
            )
        total += count
        for name in columns:
            passed[name] += file_passed[name]
            nulls[name] += file_nulls[name]
        print(f"Read {count} events from {input_path}", flush=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(output_path, input_paths, columns, total, passed, nulls)

    print(f"Read {total} events across {len(input_paths)} files and {len(columns)} trigger columns.")
    print(f"Wrote trigger efficiencies to {output_path}")


if __name__ == "__main__":
    main()
