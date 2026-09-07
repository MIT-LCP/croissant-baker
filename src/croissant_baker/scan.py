"""The scan stage: one typed entry per file found, carrying what became of it.

``discover_files`` answers which paths exist; this module answers what happened
to each of them. The vocabulary those answers are written in — :class:`Outcome`,
:class:`Reason` and :class:`ScanEntry` — lives in
:mod:`croissant_baker.entries`, and the two questions asked of a finished scan
live in :mod:`croissant_baker.duplicates` and :mod:`croissant_baker.report`.
This module is where they meet, and it re-exports all three so
``croissant_baker.scan`` stays the one import path downstream code uses.
"""

from __future__ import annotations

from typing import List, Optional

from croissant_baker.duplicates import PREFIX_BYTES, choose_primary, resolve_duplicates
from croissant_baker.entries import REASON_LABELS, Outcome, Reason, ScanEntry
from croissant_baker.files import discover_files
from croissant_baker.report import ScanReport


def scan_directory(
    dir_path: str,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
) -> List[ScanEntry]:
    """One entry per file found, in discovery order, each still ``PENDING``.

    Discovery semantics are :func:`croissant_baker.files.discover_files`'s and
    are unchanged here.

    Raises:
        FileNotFoundError: If the directory does not exist or is not a directory.
        PermissionError: If the directory cannot be accessed.
    """
    return [
        ScanEntry(path=path)
        for path in discover_files(
            dir_path,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
        )
    ]


__all__ = [
    "PREFIX_BYTES",
    "REASON_LABELS",
    "Outcome",
    "Reason",
    "ScanEntry",
    "ScanReport",
    "choose_primary",
    "resolve_duplicates",
    "scan_directory",
]
