"""What a handler is given: one file, with compression already resolved.

A :class:`FileSource` exposes the logical name and already-decompressed bytes,
and no filesystem path, so a handler cannot open the real file or observe the
wrapper.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import BinaryIO, Callable, Optional, TextIO

from croissant_baker import compression

HASH_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True, eq=False)
class FileSource:
    """One file, offered to a handler with compression already resolved.

    Attributes:
        name: The logical basename. ``data.csv`` whether the file on disk is
            ``data.csv`` or ``data.csv.gz``.
        relative_path: The logical path relative to the dataset root.
        size: Size in bytes of the file as stored, so compressed size for a
            wrapped file. This is what goes in ``contentSize``.
        exists: Whether the file was present when the source was built.

    Compared by identity: two different files can share a logical name, a size
    and an existence flag, and the openers that tell them apart are closures.
    """

    name: str
    relative_path: Path
    size: int
    exists: bool

    # Bound by make_source(). Handlers use the methods, never these.
    _open_binary: Callable[[], BinaryIO] = field(repr=False, compare=False)
    _digest: Callable[[], str] = field(repr=False, compare=False)

    @property
    def suffix(self) -> str:
        """The logical suffix, lowercased. ``.csv`` for ``data.csv.gz``."""
        return Path(self.name).suffix.lower()

    @cached_property
    def sha256(self) -> str:
        """SHA-256 of the bytes *as stored*, so the digest identifies the
        artefact that was actually acquired."""
        return self._digest()

    def open(self) -> BinaryIO:
        """Open the file as binary, already decompressed."""
        return self._open_binary()

    def open_text(self, encoding: str = compression.DEFAULT_TEXT_ENCODING) -> TextIO:
        """Open the file as text, already decompressed."""
        return io.TextIOWrapper(self._open_binary(), encoding=encoding)

    def peek(self, size: int) -> bytes:
        """Read the first ``size`` decompressed bytes, then close.

        Returns fewer bytes than asked for at end of file, and ``b""`` if the
        file cannot be read at all.
        """
        try:
            with self.open() as stream:
                return stream.read(size)
        except OSError:
            return b""


@dataclass(frozen=True, eq=False)
class PathSource(FileSource):
    """A source that also exposes a real path, for handlers that need one.

    Built only for uncompressed files, so ``path`` never points at a wrapper —
    an invariant :func:`make_source` enforces.
    """

    # eq=False must be repeated: a frozen dataclass regenerates __eq__ and
    # __hash__ on every subclass, which would restore value equality here.
    path: Path


def hash_file(real_path: Path) -> str:
    """SHA-256 of the bytes as stored, read in chunks.

    Not the decompressed bytes: that is what a user downloads and verifies, and
    it avoids decompressing gigabytes to hash them.
    """
    digest = hashlib.sha256()
    with open(real_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_source(
    real_path: Path,
    relative_path: Optional[Path] = None,
    *,
    with_path: bool = False,
) -> FileSource:
    """Build the source for a file on disk, resolving its compression.

    Args:
        real_path: The file as it exists, wrapper suffix included.
        relative_path: Its path relative to the dataset root. Defaults to the
            bare filename.
        with_path: Build a :class:`PathSource`. Only valid for an uncompressed
            file.

    Raises:
        ValueError: If ``with_path`` is asked for a compressed file.
    """
    if with_path and compression.is_compressed(real_path.name):
        raise ValueError(
            f"cannot build a PathSource for {real_path.name}: it is compressed, "
            "and a handler needing a path cannot read through the wrapper"
        )
    logical = compression.logical_name(real_path.name)
    rel = Path(relative_path) if relative_path is not None else Path(real_path.name)

    try:
        size = real_path.stat().st_size
        exists = True
    except OSError:
        size = 0
        exists = False

    common = {
        "name": logical,
        "relative_path": rel.with_name(logical),
        "size": size,
        "exists": exists,
        "_open_binary": lambda: compression.open_binary(real_path),
        "_digest": lambda: hash_file(real_path),
    }
    if with_path:
        return PathSource(**common, path=real_path)
    return FileSource(**common)
