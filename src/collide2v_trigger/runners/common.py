"""Shared compact-output machinery for the trigger application runners."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pyarrow as pa


SOURCE_ROW_COLUMN = "source_row"
AUTOMATIC_IDENTITY_COLUMNS = ("source_file", SOURCE_ROW_COLUMN, "Event_Number")
TRIGGER_PREFIX = "Trigger_"
OVERALL_COLUMN = "Trigger_passes_any"


def resolve_output_columns(
    input_schema: pa.Schema,
    requested_columns: Sequence[str],
) -> list[str]:
    """Choose compact provenance columns and validate explicit additions."""
    available = set(input_schema.names)
    missing = [name for name in requested_columns if name not in available]
    if missing:
        raise ValueError("Requested output columns are absent: " + ", ".join(missing))

    invalid = [name for name in requested_columns if name.startswith(TRIGGER_PREFIX)]
    if invalid:
        raise ValueError(
            "--keep-column cannot use reserved trigger names: " + ", ".join(invalid)
        )

    selected: list[str] = []
    for name in AUTOMATIC_IDENTITY_COLUMNS:
        if name == SOURCE_ROW_COLUMN or name in available:
            selected.append(name)
    for name in requested_columns:
        if name not in selected:
            selected.append(name)
    return selected


def compact_output_schema(
    input_schema: pa.Schema,
    trigger_names: Sequence[str],
    kept_columns: Sequence[str],
    *,
    source: str,
    menu_name: str,
    menu_metadata: Mapping[str, str] | None = None,
) -> pa.Schema:
    """Build the sidecar schema and record enough provenance to interpret it."""
    input_names = set(input_schema.names)
    fields = []
    for name in kept_columns:
        if name == SOURCE_ROW_COLUMN and name not in input_names:
            fields.append(pa.field(name, pa.int64(), nullable=False))
        else:
            fields.append(input_schema.field(name))
    fields.extend(pa.field(f"{TRIGGER_PREFIX}{name}", pa.bool_()) for name in trigger_names)
    fields.append(pa.field(OVERALL_COLUMN, pa.bool_()))

    metadata = dict(input_schema.metadata or {})
    metadata.update(
        {
            b"collide2v_trigger.format": b"trigger-sidecar-v1",
            b"collide2v_trigger.source": source.encode("utf-8"),
            b"collide2v_trigger.menu": menu_name.encode("utf-8"),
            b"collide2v_trigger.trigger_count": str(len(trigger_names)).encode("ascii"),
        }
    )
    for key, value in (menu_metadata or {}).items():
        metadata[f"collide2v_trigger.{key}".encode("utf-8")] = value.encode("utf-8")
    return pa.schema(fields, metadata=metadata)


def evaluate_compact_batch(
    batch: pa.RecordBatch,
    menu: Any,
    trigger_names: Sequence[str],
    kept_columns: Sequence[str],
    output_schema: pa.Schema,
    *,
    source_row_offset: int,
    required_columns: Sequence[str] | None = None,
) -> pa.RecordBatch:
    """Evaluate trigger bits and return only flags plus compact provenance."""
    values = {
        name: batch.column(batch.schema.get_field_index(name)).to_pylist()
        for name in (menu.required_columns if required_columns is None else required_columns)
    }
    decisions = {name: [] for name in trigger_names}
    passes_any = []

    for row_index in range(batch.num_rows):
        event = {name: column[row_index] for name, column in values.items()}
        row_bits = [menu.evaluate(name, event) for name in trigger_names]
        for name, bit in zip(trigger_names, row_bits):
            decisions[name].append(bit)
        passes_any.append(any(row_bits))

    input_names = set(batch.schema.names)
    arrays = []
    for name in kept_columns:
        if name == SOURCE_ROW_COLUMN and name not in input_names:
            arrays.append(
                pa.array(
                    range(source_row_offset, source_row_offset + batch.num_rows),
                    type=pa.int64(),
                )
            )
        else:
            arrays.append(batch.column(batch.schema.get_field_index(name)))
    arrays.extend(pa.array(decisions[name], type=pa.bool_()) for name in trigger_names)
    arrays.append(pa.array(passes_any, type=pa.bool_()))
    return pa.RecordBatch.from_arrays(arrays, schema=output_schema)
