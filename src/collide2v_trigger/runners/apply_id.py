#!/usr/bin/env python3
"""Evaluate particle-ID-based triggers with relative DZ and recomputed PUPPI HT.

The compact output contains:

* source provenance and ``Event_Number`` when available;
* ``Trigger_<trigger name>`` for each implementable trigger.
* ``Trigger_passes_any`` for the logical OR of all those trigger bits.

Additional small input columns can be retained explicitly with
``--keep-column``. Physics-object arrays are consumed while evaluating each
batch and are not copied to the output.

Processing is batched so the full (potentially multi-GB) file is never loaded
into memory at once. The input may be a local path or a ``root://`` EOS URL;
output is written to the local filesystem.

Leptons and photons are selected from L1T_PUPPIPart PID values. Isolation
is computed from nearby charged PUPPI candidates, rather than stored object
isolation branches. DZ values are assumed to be in millimeters.

The core ID-based menu disables single-object DZ checks because this dataset has
no unambiguous L1T reconstructed primary-vertex collection. Relative DZ checks
between two or more objects remain. It also replaces the stored ScalarHT value
with the sum of PUPPI AK4 jets satisfying pT > 30 GeV and |eta| < 2.4.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pyarrow.parquet as pq

from collide2v_trigger.core.id_menu import IDBasedTriggerMenu
from collide2v_trigger.io.parquet import (
    is_remote_source,
    normalize_source,
    open_parquet_file,
    source_stem,
    validate_parquet_source,
)
from collide2v_trigger.runners.common import (
    compact_output_schema,
    evaluate_compact_batch,
    resolve_output_columns,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append all available COLLIDE-2V particle-ID-based trigger decisions to Parquet, "
            "ignoring only single-object absolute-PV DZ requirements and "
            "recomputing PUPPI HT from selected jets."
        )
    )
    parser.add_argument(
        "input",
        help="Local .parquet path or root:// EOS URL",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            "Output path (default: "
            "<input stem>_trigger_bits_ID_based_no_pv_dz_recomputed_ht.parquet)"
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=256,
        help="Events processed per batch (default: 256)",
    )
    parser.add_argument(
        "--electron-iso-max",
        type=float,
        default=0.15,
        help="Maximum electron charged-PUPPI relative isolation (default: 0.15)",
    )
    parser.add_argument(
        "--photon-iso-max",
        type=float,
        default=0.15,
        help="Maximum photon charged-PUPPI relative isolation (default: 0.15)",
    )
    parser.add_argument(
        "--isolation-cone",
        type=float,
        default=0.3,
        help="Delta-R cone for charged-PUPPI isolation (default: 0.3)",
    )
    parser.add_argument(
        "--isolation-track-pt-min",
        type=float,
        default=0.0,
        help="Minimum charged-candidate pT in GeV for isolation (default: 0.0)",
    )
    parser.add_argument(
        "--keep-column",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Retain an additional input column in the compact output; repeat "
            "for multiple columns"
        ),
    )
    parser.add_argument(
        "--compression",
        default="zstd",
        choices=("zstd", "snappy", "gzip", "brotli", "none"),
        help="Output compression codec (default: zstd)",
    )
    return parser.parse_args()


def output_path_for(input_source: str, requested: Path | None) -> Path:
    if requested is not None:
        return requested.expanduser()
    filename = (
        f"{source_stem(input_source)}"
        "_trigger_bits_ID_based_no_pv_dz_recomputed_ht.parquet"
    )
    if is_remote_source(input_source):
        return Path.cwd() / filename
    return Path(input_source).with_name(filename)


def validate_paths(input_source: str, output_path: Path) -> None:
    validate_parquet_source(input_source)
    if (
        not is_remote_source(input_source)
        and Path(input_source).resolve() == output_path.resolve()
    ):
        raise ValueError(
            "Output must differ from input; refusing to overwrite the source."
        )
    if output_path.exists():
        raise FileExistsError(
            f"Output already exists: {output_path}\n"
            "Choose another path or remove the existing output explicitly."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    args = parse_args()
    input_source = normalize_source(args.input)
    output_path = output_path_for(input_source, args.output)
    validate_paths(input_source, output_path)
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be positive")

    menu = IDBasedTriggerMenu(
        electron_rel_iso_max=args.electron_iso_max,
        photon_rel_iso_max=args.photon_iso_max,
        isolation_cone=args.isolation_cone,
        isolation_track_pt_min=args.isolation_track_pt_min,
    )
    with open_parquet_file(input_source) as source:
        missing = menu.missing_required_columns(source.schema_arrow.names)
        if missing:
            raise ValueError("Input is missing required columns: " + ", ".join(missing))

        trigger_names = menu.implementable_triggers
        kept_columns = resolve_output_columns(source.schema_arrow, args.keep_column)
        output_schema = compact_output_schema(
            source.schema_arrow,
            trigger_names,
            kept_columns,
            source=input_source,
            menu_name="particle-id",
        )
        compression = None if args.compression == "none" else args.compression

        processed = 0
        writer: pq.ParquetWriter | None = None
        try:
            writer = pq.ParquetWriter(
                output_path, output_schema, compression=compression
            )
            for batch in source.iter_batches(batch_size=args.batch_size):
                output_batch = evaluate_compact_batch(
                    batch,
                    menu,
                    trigger_names,
                    kept_columns,
                    output_schema,
                    source_row_offset=processed,
                )
                writer.write_batch(output_batch)
                processed += batch.num_rows
                print(
                    f"Processed {processed}/{source.metadata.num_rows} events",
                    flush=True,
                )
        except BaseException:
            # Close the writer, but leave a partial file for diagnosis rather
            # than silently deleting user-visible data.
            if writer is not None:
                writer.close()
                writer = None
            raise
        finally:
            if writer is not None:
                writer.close()

    print(
        f"Saved {processed} compact trigger records with {len(trigger_names)} bits "
        f"to {output_path}"
    )


if __name__ == "__main__":
    main()
