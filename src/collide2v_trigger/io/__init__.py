"""Parquet input helpers for local files and XRootD/EOS URLs."""

from .parquet import (
    is_remote_source,
    normalize_source,
    open_parquet_file,
    source_filename,
    source_stem,
    validate_parquet_source,
)

__all__ = [
    "is_remote_source",
    "normalize_source",
    "open_parquet_file",
    "source_filename",
    "source_stem",
    "validate_parquet_source",
]
