"""Find one critical transverse-threshold coordinate per event.

The selected trigger is evaluated as a black box.  Its rate-setting transverse
thresholds are varied together, preserving their ratios in the chosen base
threshold profile.  The scan coordinate is the threshold (in GeV) of one
reference field.  For example, the default DoubleTkMuon_15_7 plan uses
``leading_muon_pt`` as the reference, and a scan coordinate of 30 GeV evaluates
the two legs at 30 and 14 GeV.

Only the rate-setting thresholds are scaled.  ``ht_jet_pt_min`` is a fixed jet
definition cut and is deliberately left unchanged.  Callers can select an
explicit subset with ``scale_fields`` when another calibration model is
needed.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from collide2v_trigger.core.configurable_menu import (
    ConfigurableCollide2VTriggerMenu,
)
from collide2v_trigger.core.external import create_trigger_menu
from collide2v_trigger.io.parquet import (
    is_remote_source,
    normalize_source,
    open_parquet_file,
    source_stem,
)


PT_THRESHOLD_COLUMN = "pt_threshold_gev"
DEFAULT_EOS_ENDPOINT = "root://eosproject.cern.ch"
FIXED_SCAN_FIELDS = frozenset({"ht_jet_pt_min"})


@dataclass(frozen=True)
class ThresholdScanPlan:
    """One-dimensional scaling rule for a selected trigger."""

    trigger_name: str
    reference_field: str
    scale_fields: tuple[str, ...]
    base_thresholds: Mapping[str, float]

    @property
    def reference_threshold_gev(self) -> float:
        return float(self.base_thresholds[self.reference_field])

    def thresholds_at(self, reference_threshold_gev: float) -> dict[str, float]:
        """Return the scaled fields at one reference-coordinate value."""
        coordinate = float(reference_threshold_gev)
        if not math.isfinite(coordinate) or coordinate < 0.0:
            raise ValueError("reference_threshold_gev must be finite and nonnegative")
        scale = coordinate / self.reference_threshold_gev
        return {
            field: float(self.base_thresholds[field]) * scale
            for field in self.scale_fields
        }


@dataclass(frozen=True)
class ThresholdScanResult:
    """Summary returned after writing a one-column threshold sidecar."""

    output_path: Path
    input_source: str
    trigger_name: str
    reference_field: str
    scale_fields: tuple[str, ...]
    event_count: int
    fails_at_lower_bound: int
    passes_at_upper_bound: int
    bisection_iterations: int


def resolve_input_source(
    input_file: str | Path,
    *,
    eos_endpoint: str = DEFAULT_EOS_ENDPOINT,
) -> str:
    """Accept a local path, root URL, or an ``xrdfs ls`` ``/eos/...`` line."""
    value = normalize_source(input_file)
    if "/.sys.v#." in value:
        raise ValueError(
            "The selected path is an EOS .sys.v#. version entry. Choose the "
            "ordinary minbias-*.parquet entry from the manifest instead."
        )
    if is_remote_source(value) or Path(value).is_file():
        return value
    if value.startswith("/eos/"):
        endpoint = eos_endpoint.rstrip("/")
        if not endpoint.startswith("root://"):
            raise ValueError("eos_endpoint must be a root:// URL")
        return endpoint + "/" + value
    return value


def build_threshold_scan_plan(
    menu: ConfigurableCollide2VTriggerMenu,
    trigger_name: str,
    *,
    reference_field: str | None = None,
    scale_fields: Sequence[str] | None = None,
) -> ThresholdScanPlan:
    """Resolve which fields move together and which field labels the result."""
    if trigger_name not in menu.all_triggers:
        valid = ", ".join(menu.all_triggers)
        raise KeyError(f"Unknown trigger {trigger_name!r}. Valid triggers: {valid}")
    if trigger_name not in menu.implementable_triggers:
        reason = menu.NOT_IMPLEMENTABLE_REASONS.get(trigger_name, "")
        raise ValueError(f"Trigger {trigger_name} is not implementable. {reason}".strip())

    base = menu.thresholds_for(trigger_name)
    definition = menu.custom_trigger_definitions.get(trigger_name)
    if definition is not None:
        if not definition.monotonic:
            raise ValueError(f"External trigger {trigger_name} must declare monotonic=True for bisection calibration")
        if scale_fields is not None and tuple(scale_fields) != definition.scale_fields:
            raise ValueError("External triggers must use their declared scale_fields for calibration")
        scale_fields = definition.scale_fields
        if reference_field is None:
            reference_field = definition.reference_field
    if scale_fields is None:
        selected = tuple(field for field in base if field not in FIXED_SCAN_FIELDS)
    else:
        selected = tuple(dict.fromkeys(scale_fields))
        if not selected:
            raise ValueError("scale_fields cannot be empty")
        unknown = [field for field in selected if field not in base]
        if unknown:
            raise KeyError(
                f"Unknown threshold fields for {trigger_name}: " + ", ".join(unknown)
            )

    if not selected:
        raise ValueError(f"Trigger {trigger_name} has no scannable threshold fields")

    if reference_field is None:
        # The largest positive base threshold is normally the leading leg, HT,
        # or MET threshold and gives the scan coordinate an intuitive scale.
        positive = [field for field in selected if base[field] > 0.0]
        if not positive:
            raise ValueError(
                f"Trigger {trigger_name} has no positive threshold to use as a reference"
            )
        reference_field = max(positive, key=lambda field: base[field])
    elif reference_field not in selected:
        raise ValueError(
            f"reference_field {reference_field!r} must be one of the scaled fields: "
            + ", ".join(selected)
        )
    if base[reference_field] <= 0.0:
        raise ValueError("The reference field must have a positive base threshold")

    return ThresholdScanPlan(
        trigger_name=trigger_name,
        reference_field=reference_field,
        scale_fields=selected,
        base_thresholds=dict(base),
    )


def _set_scan_coordinate(
    menu: ConfigurableCollide2VTriggerMenu,
    plan: ThresholdScanPlan,
    coordinate_gev: float,
) -> None:
    # The scanner owns this menu instance and updates only the selected seed.
    # Mutating the resolved table avoids constructing a complete menu for every
    # event and bisection step; the reviewed trigger method remains unchanged.
    resolved = menu._effective_thresholds[plan.trigger_name]
    resolved.update(plan.thresholds_at(coordinate_gev))


def _critical_threshold(
    event: Mapping[str, object],
    menu: ConfigurableCollide2VTriggerMenu,
    plan: ThresholdScanPlan,
    *,
    lower_bound_gev: float,
    upper_bound_gev: float,
    tolerance_gev: float,
    iterations: int,
) -> tuple[float, str]:
    """Return a lower-bound estimate and its censoring state for one event."""
    _set_scan_coordinate(menu, plan, lower_bound_gev)
    if not menu.evaluate(plan.trigger_name, event):
        return math.nan, "lower"

    _set_scan_coordinate(menu, plan, upper_bound_gev)
    if menu.evaluate(plan.trigger_name, event):
        return upper_bound_gev, "upper"

    low = lower_bound_gev
    high = upper_bound_gev
    for _ in range(iterations):
        if high - low <= tolerance_gev:
            break
        midpoint = (low + high) / 2.0
        _set_scan_coordinate(menu, plan, midpoint)
        if menu.evaluate(plan.trigger_name, event):
            low = midpoint
        else:
            high = midpoint
    return low, "inside"


def _output_path_for(
    input_source: str,
    trigger_name: str,
    output_file: str | Path | None,
) -> Path:
    if output_file is None:
        return Path.cwd() / f"{source_stem(input_source)}_{trigger_name}_pt_threshold.parquet"
    if is_remote_source(output_file):
        raise ValueError("Threshold output must be a local filesystem path")
    output = Path(output_file).expanduser()
    if output.suffix.lower() != ".parquet":
        raise ValueError("Threshold output must have a .parquet extension")
    return output


def _validate_scan_bounds(
    lower_bound_gev: float,
    upper_bound_gev: float,
    tolerance_gev: float,
) -> int:
    values = (lower_bound_gev, upper_bound_gev, tolerance_gev)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("Scan bounds and tolerance must be finite")
    if lower_bound_gev < 0.0:
        raise ValueError("lower_bound_gev must be nonnegative")
    if upper_bound_gev <= lower_bound_gev:
        raise ValueError("upper_bound_gev must be greater than lower_bound_gev")
    if tolerance_gev <= 0.0:
        raise ValueError("tolerance_gev must be positive")
    return max(
        0,
        math.ceil(math.log2((upper_bound_gev - lower_bound_gev) / tolerance_gev)),
    )


def scan_pt_thresholds(
    input_file: str | Path,
    trigger_name: str,
    output_file: str | Path | None = None,
    *,
    lower_bound_gev: float = 0.0,
    upper_bound_gev: float = 1000.0,
    tolerance_gev: float = 0.25,
    reference_field: str | None = None,
    scale_fields: Sequence[str] | None = None,
    batch_size: int = 256,
    electron_iso_max: float = 0.15,
    photon_iso_max: float = 0.15,
    use_rho_corrected_isolation: bool = True,
    threshold_config: str | Path | None = None,
    custom_trigger_files: Sequence[str | Path] = (),
    ignore_isolation: bool = False,
    eos_endpoint: str = DEFAULT_EOS_ENDPOINT,
    compression: str | None = "zstd",
    progress: bool = False,
) -> ThresholdScanResult:
    """Scan one Parquet file and write only one critical-threshold column.

    Rows retain the exact input order.  ``NaN`` means that the event fails even
    at the lower bound because of object multiplicity or another fixed trigger
    condition.  A value equal to the upper bound is right-censored: the event
    still passes there.  Other values are the largest threshold confirmed to
    pass, with the true pass/fail boundary less than ``tolerance_gev`` above it.

    ``input_file`` may be local, a complete ``root://`` URL, or one ordinary
    ``/eos/...`` filename copied from ``xrdfs ls`` output.  The output is always
    local and contains exactly the ``pt_threshold_gev`` data column; scan
    settings are recorded as Parquet schema metadata.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if compression not in {None, "zstd", "snappy", "gzip", "brotli"}:
        raise ValueError(f"Unsupported compression codec: {compression}")
    iterations = _validate_scan_bounds(
        lower_bound_gev, upper_bound_gev, tolerance_gev
    )
    input_source = resolve_input_source(input_file, eos_endpoint=eos_endpoint)
    output_path = _output_path_for(input_source, trigger_name, output_file)
    if output_path.exists():
        raise FileExistsError(f"Output already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    menu = create_trigger_menu(
        ignore_isolation=ignore_isolation,
        custom_trigger_files=custom_trigger_files,
        electron_iso_max=electron_iso_max,
        photon_iso_max=photon_iso_max,
        use_rho_corrected_isolation=use_rho_corrected_isolation,
        threshold_config=threshold_config,
    )
    plan = build_threshold_scan_plan(
        menu,
        trigger_name,
        reference_field=reference_field,
        scale_fields=scale_fields,
    )

    metadata = {
        b"collide2v_trigger.isolation_enabled": json.dumps(menu.isolation_enabled).encode("ascii"),
        b"collide2v_trigger.format": b"event-threshold-sidecar-v1",
        b"collide2v_trigger.source": input_source.encode("utf-8"),
        b"collide2v_trigger.trigger": trigger_name.encode("utf-8"),
        b"collide2v_trigger.target_rate_khz": str(menu.target_rate_khz(trigger_name)).encode("ascii"),
        b"collide2v_trigger.threshold_profile": menu.threshold_profile.encode("utf-8"),
        b"collide2v_trigger.reference_field": plan.reference_field.encode("utf-8"),
        b"collide2v_trigger.scale_fields": json.dumps(plan.scale_fields).encode("utf-8"),
        b"collide2v_trigger.base_thresholds_gev": json.dumps(
            plan.base_thresholds, sort_keys=True
        ).encode("utf-8"),
        b"collide2v_trigger.lower_bound_gev": str(lower_bound_gev).encode("ascii"),
        b"collide2v_trigger.upper_bound_gev": str(upper_bound_gev).encode("ascii"),
        b"collide2v_trigger.tolerance_gev": str(tolerance_gev).encode("ascii"),
        b"collide2v_trigger.nan_semantics": b"fails at lower bound",
        b"collide2v_trigger.upper_bound_semantics": b"right-censored if still passing",
    }
    if trigger_name in menu.custom_trigger_provenance:
        metadata[b"collide2v_trigger.trigger_definition"] = json.dumps(
            menu.custom_trigger_provenance[trigger_name], sort_keys=True
        ).encode("utf-8")
    required_columns = menu.required_columns_for(trigger_name)
    output_schema = pa.schema(
        [pa.field(PT_THRESHOLD_COLUMN, pa.float64(), nullable=True)],
        metadata=metadata,
    )

    processed = 0
    fails_low = 0
    passes_high = 0
    # Publish only a complete sidecar so interrupted scans can be resumed.
    temporary_output = output_path.with_suffix(output_path.suffix + ".tmp")
    writer: pq.ParquetWriter | None = None
    try:
        with open_parquet_file(input_source) as source:
            missing = sorted(set(required_columns) - set(source.schema_arrow.names))
            if missing:
                raise ValueError(
                    "Input is missing required columns: " + ", ".join(missing)
                )
            writer = pq.ParquetWriter(
                temporary_output,
                output_schema,
                compression=compression,
            )
            for batch in source.iter_batches(
                batch_size=batch_size,
                columns=required_columns,
            ):
                values = {
                    name: batch.column(batch.schema.get_field_index(name)).to_pylist()
                    for name in required_columns
                }
                thresholds: list[float] = []
                for row_index in range(batch.num_rows):
                    event = {
                        name: column[row_index]
                        for name, column in values.items()
                    }
                    threshold, state = _critical_threshold(
                        event,
                        menu,
                        plan,
                        lower_bound_gev=lower_bound_gev,
                        upper_bound_gev=upper_bound_gev,
                        tolerance_gev=tolerance_gev,
                        iterations=iterations,
                    )
                    thresholds.append(threshold)
                    fails_low += state == "lower"
                    passes_high += state == "upper"
                writer.write_batch(
                    pa.RecordBatch.from_arrays(
                        [pa.array(thresholds, type=pa.float64())],
                        schema=output_schema,
                    )
                )
                processed += batch.num_rows
                if progress:
                    print(
                        f"Processed {processed}/{source.metadata.num_rows} events",
                        flush=True,
                    )
    except BaseException:
        if writer is not None:
            writer.close()
            writer = None
        temporary_output.unlink(missing_ok=True)
        raise
    finally:
        if writer is not None:
            writer.close()

    temporary_output.replace(output_path)

    return ThresholdScanResult(
        output_path=output_path,
        input_source=input_source,
        trigger_name=trigger_name,
        reference_field=plan.reference_field,
        scale_fields=plan.scale_fields,
        event_count=processed,
        fails_at_lower_bound=fails_low,
        passes_at_upper_bound=passes_high,
        bisection_iterations=iterations,
    )
