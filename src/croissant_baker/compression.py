"""Compression handling, in one place.

Compression is a transport wrapper, not a format: ``data.csv.gz`` is a CSV that
happens to have arrived gzipped. This module owns which compressions exist,
their suffixes and media types, and how to get a decompressed stream.

Scope is single-file wrappers. ``.zip`` and ``.tar`` hold several members; they
are reported, not opened, and are deliberately absent here.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
from dataclasses import dataclass
from pathlib import Path
from typing import (
    BinaryIO,
    Callable,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
)

#: ``utf-8-sig`` transparently drops a UTF-8 BOM, which files exported from
#: Windows tooling routinely carry.
DEFAULT_TEXT_ENCODING = "utf-8-sig"


@dataclass(frozen=True)
class Compression:
    """One supported compression wrapper.

    ``media_type`` accompanies the format's own media type rather than
    replacing it. ``opener`` has the signature of :func:`gzip.open`.
    """

    name: str
    suffix: str
    media_type: str
    opener: Callable[..., object]

    def open_binary(self, path: Path) -> BinaryIO:
        return self.opener(path, "rb")  # type: ignore[return-value]


GZIP = Compression("gzip", ".gz", "application/gzip", gzip.open)
BZIP2 = Compression("bzip2", ".bz2", "application/x-bzip2", bz2.open)
XZ = Compression("xz", ".xz", "application/x-xz", lzma.open)

BUILTIN_COMPRESSIONS: Tuple[Compression, ...] = (GZIP, BZIP2, XZ)

_registry: List[Compression] = list(BUILTIN_COMPRESSIONS)


def register_compression(comp: Compression) -> None:
    """Add a compression to the registry.

    Everything downstream follows: dispatch strips the new suffix, streams
    decompress through it, ``encodingFormat`` gains its media type, and FileSet
    globs expand to cover it. No handler changes.
    """
    # Replaced in place, so match order stays stable across a re-registration.
    for i, existing in enumerate(_registry):
        if existing.suffix == comp.suffix:
            _registry[i] = comp
            return
    _registry.append(comp)


def compressions() -> Tuple[Compression, ...]:
    """Every registered compression, in match order."""
    return tuple(_registry)


#: Suffixes that hold several members rather than wrapping one file. Compound
#: forms are absent on purpose: ``.tar.gz`` is a gzipped ``.tar``, and
#: :func:`is_archive` strips the wrapper before looking.
ARCHIVE_SUFFIXES: Tuple[str, ...] = (".zip", ".tar", ".tgz")


def split_compression(name: str) -> Tuple[str, Optional[Compression]]:
    """Split a filename into its logical name and its compression, if any.

    Exactly one wrapper is removed, so ``a.tar.gz`` yields ``("a.tar", GZIP)``.
    """
    lowered = name.lower()
    for comp in _registry:
        if lowered.endswith(comp.suffix) and len(name) > len(comp.suffix):
            return name[: -len(comp.suffix)], comp
    return name, None


def logical_name(name: str) -> str:
    """Return ``name`` with any compression suffix removed."""
    return split_compression(name)[0]


def compression_for(name: str) -> Optional[Compression]:
    """Return the compression wrapping ``name``, or ``None`` if it is plain."""
    return split_compression(name)[1]


def is_compressed(name: str) -> bool:
    return compression_for(name) is not None


def is_archive(name: str) -> bool:
    """Whether ``name`` is a multi-member archive the baker will not open.

    Asked of the stored name first, so ``.tgz`` survives a compression later
    registering that suffix; then of the logical name, so ``bundle.tar.xz`` is
    recognised as the ``.tar`` it wraps.
    """
    if name.lower().endswith(ARCHIVE_SUFFIXES):
        return True
    return logical_name(name).lower().endswith(ARCHIVE_SUFFIXES)


def expand_globs(patterns: Sequence[str], wrappers: Iterable[Compression]) -> List[str]:
    """Return ``patterns`` plus one variant per compression in ``wrappers``.

    ``**/*.dcm`` matches ``img.dcm`` but not ``img.dcm.gz``, so a compressed
    file would be described and then excluded from its own FileSet.

    ``wrappers`` is the compressions actually present among the files being
    described, so a dataset with none gets its patterns back unchanged.
    """
    present = list(wrappers)
    out: List[str] = []
    for pattern in patterns:
        out.append(pattern)
        if is_compressed(pattern):
            continue
        out.extend(f"{pattern}{c.suffix}" for c in present)
    return out


def open_binary(path: Path) -> BinaryIO:
    """Open ``path`` as binary, transparently decompressing a wrapper.

    Forward-only: gzip, bzip2 and xz have no random access, so a reader that
    seeks pays a decompression pass per seek.
    """
    comp = compression_for(path.name)
    return comp.open_binary(path) if comp else open(path, "rb")
