"""Reading HDF5 structure from a stream, as a finite tree of nodes.

The offset-tolerant signature search, opening a file-like object, and a
:class:`~croissant_baker.handlers.layouts.Node` view over h5py. What a
container's structure *means* is
:mod:`~croissant_baker.handlers.layouts`, which is why that module does not
import h5py.

Everything here comes out of object headers and no array is ever read, so
describing a 5 GB file costs what describing a 5 MB one with the same structure
costs.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, Optional, Tuple

import h5py
import numpy as np

from croissant_baker.handlers.layouts import (
    BROKEN,
    COMPOUND,
    CYCLE,
    EXTERNAL,
    OPAQUE,
    REFERENCE,
    STRING,
)
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

#: The eight bytes every HDF5 superblock opens with.
SIGNATURE = b"\x89HDF\r\n\x1a\n"

#: How far in to look for it. HDF5 permits a user block before the superblock,
#: which then sits at 0 or at 512·2ⁿ, so a check of ``bytes[:8]`` alone rejects
#: every MATLAB v7.3 file — MATLAB puts a text header in a 512-byte block.
PEEK_BYTES = 8192

#: The offsets a superblock can take within :data:`PEEK_BYTES`. The bound is
#: honest rather than conclusive: HDF5 sets no upper limit on a user block, so a
#: legal file with a larger one is not claimed even though h5py could open it.
#: Widening it means a claim-time reason channel, which is a contract change.
_OFFSETS = (0, 512, 1024, 2048, 4096)


def looks_like_hdf5(prefix: bytes) -> bool:
    """Whether ``prefix`` carries an HDF5 superblock at a permitted offset."""
    found = any(
        prefix[offset : offset + len(SIGNATURE)] == SIGNATURE for offset in _OFFSETS
    )
    if not found and len(prefix) >= PEEK_BYTES:
        logger.debug(
            "no HDF5 signature in the first %d bytes, at any of the offsets "
            "%s; a larger user block is legal but is not searched for",
            PEEK_BYTES,
            _OFFSETS,
        )
    return found


@contextmanager
def opened(source: FileSource) -> Iterator["H5Node"]:
    """Open ``source`` and yield its root node.

    h5py takes a file-like object, and asks it only for ``read``, ``readinto``,
    ``seek`` and ``tell`` — never ``fileno`` — so a compressed file works. What
    a wrapper costs is the total distance h5py seeks, which a non-seekable codec
    pays by decompressing and discarding.
    """
    with source.open() as stream, h5py.File(stream, "r") as handle:
        # The root is on its own chain, so a link back to it is a cycle like
        # any other rather than one extra level of walking.
        identity = _address(handle)
        yield H5Node(handle, "", frozenset({identity}), identity=identity)


class H5Node:
    """One HDF5 object, seen as a :class:`layouts.Node`.

    Constructed with the identities of the nodes on the path to it, so a soft
    link back to one of them is reported as a cycle instead of followed. That
    keeps the tree finite for every consumer rather than only for the one walk
    that remembered to guard.
    """

    def __init__(
        self,
        obj,
        path: str,
        ancestors: frozenset,
        *,
        name: str = "",
        unresolved: str = "",
        identity: object = None,
    ) -> None:
        self._obj = obj
        self.name = name
        self.path = path
        self.unresolved = unresolved
        self.identity = identity
        self._ancestors = ancestors

    # -- what it is -------------------------------------------------------

    @property
    def dtype(self) -> Optional[str]:
        """The normalised element type, or None for anything but a dataset."""
        if not isinstance(self._obj, h5py.Dataset):
            return None
        return _normalise(self._obj.dtype)[0]

    @property
    def shape(self) -> Optional[Tuple[int, ...]]:
        if not isinstance(self._obj, h5py.Dataset):
            return None
        ragged = _normalise(self._obj.dtype)[1]
        # A variable-length dataset has one more dimension than it declares,
        # and only the declared ones have a size.
        return tuple(self._obj.shape) + ((-1,) if ragged else ())

    @property
    def fields(self) -> Optional[Tuple[Tuple[str, str], ...]]:
        if not isinstance(self._obj, h5py.Dataset):
            return None
        dtype = self._obj.dtype
        if dtype.names is None:
            return None
        return tuple(
            (name, _normalise(dtype.fields[name][0])[0]) for name in dtype.names
        )

    def attr(self, name: str) -> Optional[object]:
        """The named attribute, decoded to plain Python, or None.

        One at a time, because an attribute holds a *value* and values here can
        be large: a file of 8 MB of root attributes over a one-byte dataset
        costs 5 KiB read this way and the whole 8 MB read as a mapping. That is
        the difference between describing structure and reading the file.
        """
        if self._obj is None or name not in self._obj.attrs:
            return None
        return _decode(self._obj.attrs[name])

    # -- children ---------------------------------------------------------

    def keys(self) -> Tuple[str, ...]:
        """Child names, sorted.

        Sorted rather than in the container's order: HDF5 returns names
        alphabetically by default but insertion-ordered for a group written with
        ``track_order``, and the manifest must not record which.
        """
        if not isinstance(self._obj, h5py.Group):
            return ()
        return tuple(sorted(self._obj.keys()))

    def child(self, name: str) -> Optional["H5Node"]:
        """The named child, with its link classified before it is resolved."""
        if not isinstance(self._obj, h5py.Group) or name not in self._obj:
            return None
        path = f"{self.path}/{name}" if self.path else name

        # get(getlink=True) never raises, and classifying before resolving is
        # what keeps an external link from being followed by accident.
        link = self._obj.get(name, getlink=True)
        if isinstance(link, h5py.ExternalLink):
            # Never followed, and its target is not recorded either: a *valid*
            # external link can name any HDF5 file on the machine, so following
            # it would describe structure the dataset does not contain and
            # disclose a path outside its root.
            return self._link(name, path, EXTERNAL)
        try:
            obj = self._obj[name]
        except KeyError:
            # A soft link with no target. h5py raises here and not from
            # getlink, so this is where a dangling link stops costing anything.
            logger.debug("%s: link target is absent", path)
            return self._link(name, path, BROKEN)

        identity = _address(obj)
        if identity in self._ancestors:
            # A soft link back to a node on the path to it. Following it does
            # not terminate, and its target is described where it was reached.
            return self._link(name, path, CYCLE)
        return H5Node(
            obj,
            path,
            self._ancestors | {identity},
            name=name,
            identity=identity,
        )

    def _link(self, name: str, path: str, unresolved: str) -> "H5Node":
        return H5Node(None, path, frozenset(), name=name, unresolved=unresolved)


# ---------------------------------------------------------------------------
# numpy, kept on this side of the seam
# ---------------------------------------------------------------------------


def _address(obj) -> object:
    """Where the object lives in the file: equal for every path to it."""
    try:
        return h5py.h5o.get_info(obj.id).addr
    except Exception:  # pragma: no cover - h5py answers for every object kind
        return obj.name


def _normalise(dtype) -> Tuple[str, bool]:
    """``(normalised name, whether it is variable-length)`` for an HDF5 dtype.

    ``dtype.kind`` alone cannot do this. An object reference and a
    variable-length string both report ``'O'``, and calling a reference text
    would invite a reader to expect labels where there are only pointers. Only
    h5py's own dtype checks part them.
    """
    if dtype.names is not None:
        return COMPOUND, False
    if h5py.check_string_dtype(dtype) is not None:
        return STRING, False
    if h5py.check_ref_dtype(dtype) is not None:
        return REFERENCE, False
    inner = h5py.check_vlen_dtype(dtype)
    if inner is not None:
        return _normalise(np.dtype(inner))[0], True
    if dtype.kind == "V":
        return OPAQUE, False
    # Whatever numpy calls it. An HDF5 enum arrives as the integer it is
    # stored as, which is the honest answer: the names are values, and the
    # two-value enum h5py writes for a boolean it maps back to bool itself.
    return dtype.name, False


def _decode(value):
    """One attribute value as plain Python, or None if it carries no value."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.ndarray):
        if value.dtype.kind == "O" and h5py.check_string_dtype(value.dtype) is None:
            return None
        return [_decode(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return _decode(value.item())
    if isinstance(value, (h5py.Reference, h5py.RegionReference)):
        # A reference names something rather than holding a value. The key
        # stays, so a reader can see it was there.
        return None
    return value
