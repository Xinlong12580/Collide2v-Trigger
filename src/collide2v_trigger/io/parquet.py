"""Open local or XRootD/EOS Parquet inputs with one small interface.

Remote inputs use the same mechanism as ``Set_Based_Models/data_conversion``:
``fsspec.core.url_to_fs`` resolves the URL and ``fsspec-xrootd`` supplies the
``root://`` filesystem. The returned file handle stays open for the complete
``ParquetFile`` lifetime because Arrow performs lazy seeks between row groups.

Outputs are deliberately outside this module. The trigger applications write
local Parquet files, matching the data-conversion package's EOS-to-local flow.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator
from urllib.parse import urlsplit

import pyarrow.parquet as pq


def is_remote_source(source: str | Path) -> bool:
    """Return whether *source* is a URL rather than a local filesystem path."""
    return "://" in str(source)


def normalize_source(source: str | Path) -> str:
    """Expand ``~`` for local paths while preserving remote URLs byte-for-byte."""
    value = str(source)
    return value if is_remote_source(value) else str(Path(value).expanduser())


def source_filename(source: str | Path) -> str:
    """Return the final filename component of a local path or remote URL."""
    value = normalize_source(source)
    if is_remote_source(value):
        name = PurePosixPath(urlsplit(value).path.rstrip("/")).name
    else:
        name = Path(value).name
    if not name:
        raise ValueError(f"Input does not identify a file: {source}")
    return name


def source_stem(source: str | Path) -> str:
    """Return the input filename without its final extension."""
    return Path(source_filename(source)).stem


def validate_parquet_source(source: str | Path) -> str:
    """Validate a local file or the syntax of a remote Parquet URL.

    Opening a remote URL is deferred to ``open_parquet_file``. That operation
    gives the authoritative existence and authentication errors from XRootD.
    """
    value = normalize_source(source)
    if Path(source_filename(value)).suffix.lower() != ".parquet":
        raise ValueError(f"Input must be a .parquet file: {value}")
    if not is_remote_source(value) and not Path(value).is_file():
        raise FileNotFoundError(f"Input file does not exist: {value}")
    return value


@contextmanager
def open_parquet_file(source: str | Path) -> Iterator[pq.ParquetFile]:
    """Yield a ``ParquetFile`` backed by a local path or an fsspec URL."""
    value = validate_parquet_source(source)
    if not is_remote_source(value):
        with pq.ParquetFile(value) as parquet_file:
            yield parquet_file
        return

    try:
        import fsspec
    except ImportError as exc:
        raise RuntimeError(
            "Remote Parquet input requires fsspec and, for root:// EOS URLs, "
            "fsspec-xrootd. Install requirements-xrootd.txt."
        ) from exc

    try:
        filesystem, filesystem_path = fsspec.core.url_to_fs(value)
    except (ImportError, ValueError) as exc:
        raise RuntimeError(
            f"Could not create a filesystem for {value!r}. For root:// URLs, "
            "install fsspec-xrootd and make sure the XRootD bindings are usable."
        ) from exc

    with filesystem.open(filesystem_path, "rb") as handle:
        with pq.ParquetFile(handle) as parquet_file:
            yield parquet_file

