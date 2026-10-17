"""Run the one-file threshold scanner over a sample of an EOS directory."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import random
from typing import Sequence
from urllib.parse import urlsplit

import pyarrow.parquet as pq

from collide2v_trigger.core.external import create_trigger_menu, definition_identity

from collide2v_trigger.io.parquet import (
    is_remote_source,
    normalize_source,
    source_filename,
    source_stem,
)

from .threshold_scan import (
    DEFAULT_EOS_ENDPOINT,
    ThresholdScanResult,
    build_threshold_scan_plan,
    scan_pt_thresholds,
)


@dataclass(frozen=True)
class DirectoryThresholdScanResult:
    """Summary of a multi-file directory scan."""

    input_directory: str
    trigger_name: str
    available_file_count: int
    selected_files: tuple[str, ...]
    completed_scans: tuple[ThresholdScanResult, ...]
    skipped_outputs: tuple[Path, ...]


def resolve_input_directory(
    input_directory: str | Path,
    *,
    eos_endpoint: str = DEFAULT_EOS_ENDPOINT,
) -> str:
    """Resolve a local directory, root URL, or unmounted ``/eos/...`` path."""
    value = normalize_source(input_directory).rstrip("/")
    if is_remote_source(value):
        if not value.startswith("root://"):
            raise ValueError("Remote input directory must use a root:// URL")
        return value
    if Path(value).is_dir():
        return value
    if value.startswith("/eos/"):
        endpoint = eos_endpoint.rstrip("/")
        if not endpoint.startswith("root://"):
            raise ValueError("eos_endpoint must be a root:// URL")
        return endpoint + "/" + value
    raise NotADirectoryError(f"Input directory does not exist: {value}")


def _root_url_for_path(directory_url: str, filesystem_path: str) -> str:
    parsed = urlsplit(directory_url)
    endpoint = f"{parsed.scheme}://{parsed.netloc}"
    return endpoint + "//" + filesystem_path.lstrip("/")


def list_parquet_files(
    input_directory: str | Path,
    *,
    eos_endpoint: str = DEFAULT_EOS_ENDPOINT,
) -> list[str]:
    """List ordinary Parquet files immediately below a local or EOS directory."""
    directory = resolve_input_directory(
        input_directory,
        eos_endpoint=eos_endpoint,
    )
    if not is_remote_source(directory):
        entries = [str(path) for path in Path(directory).iterdir() if path.is_file()]
    else:
        try:
            import fsspec
        except ImportError as exc:
            raise RuntimeError(
                "EOS directory listing requires fsspec and fsspec-xrootd. "
                "Install the project's 'eos' optional dependencies."
            ) from exc
        try:
            filesystem, filesystem_path = fsspec.core.url_to_fs(directory)
            listed = filesystem.ls(filesystem_path, detail=False)
        except (ImportError, OSError, ValueError) as exc:
            raise RuntimeError(f"Could not list EOS directory {directory!r}") from exc
        entries = []
        for entry in listed:
            value = str(entry)
            if is_remote_source(value):
                entries.append(value)
            else:
                # fsspec-xrootd currently returns bare filenames from ls(),
                # while other filesystems may return absolute paths.
                if not value.startswith("/"):
                    value = filesystem_path.rstrip("/") + "/" + value
                entries.append(_root_url_for_path(directory, value))

    ordinary = {
        entry
        for entry in entries
        if source_filename(entry).lower().endswith(".parquet")
        and "/.sys.v#." not in entry
        and not source_filename(entry).startswith(".sys.v#.")
    }
    files = sorted(ordinary, key=lambda item: (source_filename(item), item))
    if not files:
        raise FileNotFoundError(f"No ordinary .parquet files found under {directory}")
    return files


def select_parquet_files(
    available_files: Sequence[str],
    *,
    n_files: int = 10,
    selection: str = "random",
    random_seed: int = 12345,
) -> list[str]:
    """Choose a deterministic ordered or random subset of available files."""
    if n_files <= 0:
        raise ValueError("n_files must be positive")
    if selection not in {"random", "first"}:
        raise ValueError("selection must be 'random' or 'first'")
    count = min(n_files, len(available_files))
    if selection == "first":
        return list(available_files[:count])
    shuffled = list(available_files)
    random.Random(random_seed).shuffle(shuffled)
    return shuffled[:count]


def scan_eos_directory(
    input_directory: str | Path,
    trigger_name: str,
    output_directory: str | Path = "results/threshold_scans",
    *,
    n_files: int = 10,
    selection: str = "random",
    random_seed: int = 12345,
    skip_existing: bool = True,
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
) -> DirectoryThresholdScanResult:
    """Scan one trigger on a selected sample of files from an EOS directory.

    Each selected source produces one local one-column Parquet sidecar by
    calling :func:`scan_pt_thresholds`. Existing outputs are skipped by default
    so an interrupted directory scan can be resumed safely.
    """
    if is_remote_source(output_directory):
        raise ValueError("output_directory must be a local filesystem path")
    output_root = Path(output_directory).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)
    menu = create_trigger_menu(
        ignore_isolation=ignore_isolation,
        custom_trigger_files=custom_trigger_files, threshold_config=threshold_config,
        electron_iso_max=electron_iso_max, photon_iso_max=photon_iso_max,
        use_rho_corrected_isolation=use_rho_corrected_isolation,
    )
    plan = build_threshold_scan_plan(menu, trigger_name,
                                     reference_field=reference_field, scale_fields=scale_fields)

    directory = resolve_input_directory(
        input_directory,
        eos_endpoint=eos_endpoint,
    )
    available = list_parquet_files(directory, eos_endpoint=eos_endpoint)
    selected = select_parquet_files(
        available,
        n_files=n_files,
        selection=selection,
        random_seed=random_seed,
    )

    completed: list[ThresholdScanResult] = []
    skipped: list[Path] = []
    for file_index, input_file in enumerate(selected, start=1):
        output_file = output_root / (
            f"{source_stem(input_file)}_{trigger_name}_pt_threshold.parquet"
        )
        if output_file.exists() and skip_existing:
            metadata = dict(pq.read_schema(output_file).metadata or {})
            if json.loads(metadata.get(b"collide2v_trigger.isolation_enabled", b"true")) != menu.isolation_enabled:
                raise ValueError(f"Existing score has a different isolation setting: {output_file}; use a new output directory")
            if trigger_name in menu.custom_trigger_provenance or ignore_isolation:
                stored = json.loads(metadata.get(b"collide2v_trigger.trigger_definition", b"null"))
                expected = menu.custom_trigger_provenance.get(trigger_name)
                settings = {
                    "source": input_file, "trigger": trigger_name,
                    "reference_field": plan.reference_field,
                    "threshold_profile": menu.threshold_profile,
                }
                consistent = (definition_identity(stored) if stored else None) == (definition_identity(expected) if expected else None)
                consistent = consistent and all(
                    metadata.get(f"collide2v_trigger.{key}".encode(), b"").decode() == value
                    for key, value in settings.items()
                )
                consistent = consistent and all(
                    float(metadata.get(f"collide2v_trigger.{key}".encode(), b"nan")) == value
                    for key, value in {"lower_bound_gev": lower_bound_gev,
                                       "upper_bound_gev": upper_bound_gev,
                                       "tolerance_gev": tolerance_gev}.items()
                )
                consistent = consistent and json.loads(metadata.get(b"collide2v_trigger.base_thresholds_gev", b"null")) == dict(plan.base_thresholds)
                consistent = consistent and json.loads(metadata.get(b"collide2v_trigger.scale_fields", b"null")) == list(plan.scale_fields)
                if not consistent:
                    raise ValueError(f"Existing external-trigger score has a different definition or scan settings: {output_file}; use a new output directory")
            skipped.append(output_file)
            if progress:
                print(
                    f"[{file_index}/{len(selected)}] Skipping existing {output_file}",
                    flush=True,
                )
            continue
        if progress:
            print(
                f"[{file_index}/{len(selected)}] Scanning {input_file}",
                flush=True,
            )
        result = scan_pt_thresholds(
            input_file=input_file,
            trigger_name=trigger_name,
            output_file=output_file,
            lower_bound_gev=lower_bound_gev,
            upper_bound_gev=upper_bound_gev,
            tolerance_gev=tolerance_gev,
            reference_field=reference_field,
            scale_fields=scale_fields,
            batch_size=batch_size,
            electron_iso_max=electron_iso_max,
            photon_iso_max=photon_iso_max,
            use_rho_corrected_isolation=use_rho_corrected_isolation,
            threshold_config=threshold_config,
            custom_trigger_files=custom_trigger_files,
            ignore_isolation=ignore_isolation,
            eos_endpoint=eos_endpoint,
            compression=compression,
            progress=progress,
        )
        completed.append(result)

    return DirectoryThresholdScanResult(
        input_directory=directory,
        trigger_name=trigger_name,
        available_file_count=len(available),
        selected_files=tuple(selected),
        completed_scans=tuple(completed),
        skipped_outputs=tuple(skipped),
    )
