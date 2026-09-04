"""Reading HDF5 structure: the signature, the tree walk, and the links.

This layer knows HDF5 and nothing about what the structure means, so what it
produces is asserted here in HDF5's own vocabulary — paths, dtypes, shapes.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import h5py
import numpy as np
import pytest

from croissant_baker.handlers import hdf5, layouts
from croissant_baker.sources import make_source

from tests import hdf5_fixtures as fx


@dataclass(frozen=True)
class Snapshot:
    """One node's properties, read while the file was still open.

    Every property of a live node reaches into h5py, so a test holding one past
    the ``with`` block would be asserting on a closed file.
    """

    path: str
    dtype: Optional[str]
    shape: Optional[tuple]
    fields: Optional[tuple]
    unresolved: str
    names: tuple

    def keys(self) -> tuple:
        return self.names


def root_of(path: Path) -> dict:
    """Every node of ``path``, keyed by the path the walk reached it at."""
    with hdf5.opened(make_source(path)) as root:
        return _flatten(root)


def _flatten(node, out=None) -> dict:
    out = {} if out is None else out
    for name in node.keys():
        child = node.child(name)
        out[child.path] = Snapshot(
            path=child.path,
            dtype=child.dtype,
            shape=child.shape,
            fields=child.fields,
            unresolved=child.unresolved,
            names=child.keys(),
        )
        if child.dtype is None and not child.unresolved:
            _flatten(child, out)
    return out


# ---------------------------------------------------------------------------
# Claiming: the signature is not always at offset 0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("userblock", [0, 512, 4096])
def test_the_signature_is_found_behind_a_user_block(
    userblock: int, tmp_path: Path
) -> None:
    """None, MATLAB v7.3's, and the last offset searched.

    MATLAB puts a text header in a 512-byte user block, so a check of
    ``bytes[:8]`` alone rejects every file it writes.
    """
    path = tmp_path / "probe.h5"
    with h5py.File(path, "w", userblock_size=userblock) as f:
        f["x"] = np.arange(3)
    if userblock:
        with open(path, "r+b") as fh:
            fh.write(b"a text header written into the user block")

    assert hdf5.looks_like_hdf5(path.read_bytes()[: hdf5.PEEK_BYTES])
    assert root_of(path)["x"].shape == (3,)


def test_a_user_block_past_the_peek_is_not_claimed(tmp_path: Path) -> None:
    """HDF5 sets no upper limit on a user block, so the 8 KiB bound is honest
    rather than conclusive. No writer is known to exceed it."""
    path = tmp_path / "far.h5"
    with h5py.File(path, "w", userblock_size=hdf5.PEEK_BYTES * 2) as f:
        f["x"] = np.arange(3)

    assert not hdf5.looks_like_hdf5(path.read_bytes()[: hdf5.PEEK_BYTES])


@pytest.mark.parametrize(
    "prefix",
    [b"", b"\x89HDF", b"not hdf5 at all", b"\x00" * 512 + b"\x89HDF\r\n\x1a\x00"],
)
def test_bytes_that_are_not_hdf5_are_not_claimed(prefix: bytes) -> None:
    assert not hdf5.looks_like_hdf5(prefix)


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def test_every_dataset_is_reached_at_every_depth(tmp_path: Path) -> None:
    nodes = root_of(fx.write_plain(tmp_path / "plain.h5"))

    assert nodes["top"].shape == (4,)
    assert nodes["group/middle"].shape == (2, 3)
    assert nodes["group/deeper/bottom"].shape == (5, 6, 7)
    assert nodes["group"].dtype is None
    assert nodes["group/deeper/bottom"].dtype == "float64"


def test_children_come_back_in_one_order_whatever_the_file(tmp_path: Path) -> None:
    """HDF5 returns names alphabetically by default, but insertion-ordered for
    a group written with ``track_order`` — and the manifest must not record
    which of the two the writer chose."""
    path = tmp_path / "tracked.h5"
    with h5py.File(path, "w") as f:
        group = f.create_group("g", track_order=True)
        for name in ("zebra", "apple", "mango"):
            group[name] = np.arange(2)
    with h5py.File(path, "r") as raw:
        assert list(raw["g"]) == ["zebra", "apple", "mango"], "the file kept its order"

    assert list(root_of(path)) == ["g", "g/apple", "g/mango", "g/zebra"]


# ---------------------------------------------------------------------------
# Links are classified, and two of them are never followed
# ---------------------------------------------------------------------------


def test_a_soft_link_is_followed_and_an_external_one_is_not(tmp_path: Path) -> None:
    """A safety boundary, not a robustness one. A *valid* external link can
    point at any HDF5 file on the machine, so following it would describe
    structure the dataset does not hold and disclose a path outside its root.
    """
    target = tmp_path / "outside" / "target.h5"
    target.parent.mkdir()
    nodes = root_of(fx.write_links(tmp_path / "links.h5", target))

    assert nodes["soft"].shape == (3,)
    assert nodes["external"].unresolved == layouts.EXTERNAL
    assert (nodes["external"].dtype, nodes["external"].shape) == (None, None)
    assert nodes["external"].keys() == ()
    assert not any("secret" in path for path in nodes)


def test_a_broken_link_costs_only_itself(tmp_path: Path) -> None:
    """h5py raises ``KeyError`` on resolving one, which must not end the walk."""
    nodes = root_of(fx.write_links(tmp_path / "links.h5", tmp_path / "target.h5"))

    assert nodes["soft_broken"].unresolved == layouts.BROKEN
    # An external link is classified before it is resolved, so a dangling one is
    # indistinguishable from a valid one — and neither is followed.
    assert nodes["external_broken"].unresolved == layouts.EXTERNAL
    assert nodes["described/real"].shape == (3,), "the rest was still described"


def test_a_soft_link_cycle_terminates(tmp_path: Path) -> None:
    """``described/cycle`` points at ``/described``."""
    nodes = root_of(fx.write_links(tmp_path / "links.h5", tmp_path / "target.h5"))

    assert nodes["described/cycle"].unresolved == layouts.CYCLE
    assert not [path for path in nodes if path.count("cycle") > 1]


def test_a_link_back_to_the_root_is_a_cycle_too(tmp_path: Path) -> None:
    """The root is on its own ancestor chain. Left off it, a link pointing at
    the root would be followed once and describe the whole file twice."""
    path = tmp_path / "rooted.h5"
    with h5py.File(path, "w") as f:
        group = f.create_group("a")
        group["data"] = np.arange(2)
        group["up"] = h5py.SoftLink("/")

    nodes = root_of(path)

    assert nodes["a/up"].unresolved == layouts.CYCLE
    assert sorted(nodes) == ["a", "a/data", "a/up"]


# ---------------------------------------------------------------------------
# Dtypes, normalised so that nothing downstream needs numpy
# ---------------------------------------------------------------------------


def test_every_dtype_is_normalised_to_one_vocabulary(tmp_path: Path) -> None:
    """``(dtype, shape, fields)`` for one dataset per row of the mapping.

    A reference and a variable-length string both report ``kind == 'O'``, so a
    check of the kind would call an opaque reference text.
    """
    nodes = root_of(fx.write_dtypes(tmp_path / "dtypes.h5"))

    assert {
        path: (node.dtype, node.shape, node.fields)
        for path, node in nodes.items()
        if node.dtype
    } == {
        "ints/i8": ("int8", (2,), None),
        "ints/i64": ("int64", (2,), None),
        "ints/u16": ("uint16", (2,), None),
        "floats/f16": ("float16", (2,), None),
        "floats/f64": ("float64", (2,), None),
        "flag": ("bool", (2,), None),
        "scalar": ("int64", (), None),
        "text/vlen": (layouts.STRING, (2,), None),
        "text/fixed": (layouts.STRING, (2,), None),
        "opaque/refs": (layouts.REFERENCE, (2,), None),
        # Two dimensions, and only the first has a declared size.
        "opaque/ragged": ("int32", (2, -1), None),
        "opaque/bytes": (layouts.OPAQUE, (), None),
        "table": (
            layouts.COMPOUND,
            (3,),
            (("index", layouts.STRING), ("age", "int64"), ("score", "float32")),
        ),
    }


# ---------------------------------------------------------------------------
# Attributes, read one at a time and never as a set
# ---------------------------------------------------------------------------


def test_an_attribute_is_decoded_to_plain_python(tmp_path: Path) -> None:
    """Numpy scalars and byte strings both reach descriptions and identifier
    logic, so the layout reader is handed neither."""
    path = tmp_path / "attrs.h5"
    with h5py.File(path, "w") as f:
        dataset = f.create_dataset("x", data=np.arange(2))
        dataset.attrs["count"] = np.int64(3)
        dataset.attrs["ratio"] = np.float32(0.5)
        dataset.attrs["label"] = b"bytes"
        dataset.attrs["names"] = np.asarray(["a", "b"], dtype=object)
        dataset.attrs["dims"] = np.asarray([4, 5], dtype="int32")
        dataset.attrs["flag"] = np.bool_(True)
        # A reference names something rather than holding a value, and presence
        # is tested as ``attr(name) is not None``, so it reads as absent.
        dataset.attrs["points_at"] = dataset.ref

    with hdf5.opened(make_source(path)) as root:
        node = root.child("x")
        names = ("count", "ratio", "label", "names", "dims", "flag")
        read = {name: node.attr(name) for name in (*names, "points_at", "absent")}

    assert read == {
        "count": 3,
        "ratio": pytest.approx(0.5),
        "label": "bytes",
        "names": ["a", "b"],
        "dims": [4, 5],
        "flag": True,
        "points_at": None,
        "absent": None,
    }


def test_asking_for_one_attribute_does_not_read_the_others(tmp_path: Path) -> None:
    """Eight megabytes of root attributes over a one-byte dataset. Reading
    them all is reading the file, which is why there is no mapping over them."""
    path = fx.write_fat_attributes(tmp_path / "fat_attrs.h5")
    payload = path.stat().st_size
    assert payload > 8_000_000

    source, counters = counting_source(path)
    with hdf5.opened(source) as root:
        assert root.attr("encoding-type") is not None
    read = sum(counter.count for counter in counters)

    assert read < payload / 100, f"read {read} of {payload}"


def test_the_container_is_closed_once_it_has_been_described(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """h5py holds an OS file handle per open container, and a GEO series is
    hundreds of them in one bake.

    The spy keeps a reference to every handle, so the only thing that can call
    ``close`` is the ``with`` — a collected handle would close itself and the
    assertion would pass for the wrong reason.
    """
    handles: list = []
    real = h5py.File

    class _Spy(real):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.was_closed = False
            handles.append(self)

        def close(self, *args, **kwargs):
            self.was_closed = True
            return super().close(*args, **kwargs)

    monkeypatch.setattr(h5py, "File", _Spy)

    with hdf5.opened(make_source(fx.write_plain(tmp_path / "probe.h5"))) as root:
        assert root.keys()

    assert handles, "nothing opened an h5py.File"
    assert all(handle.was_closed for handle in handles)


class ReadCounter(io.RawIOBase):
    """A stream that records how many bytes were pulled through it."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.count = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self.inner.read(size)
        self.count += len(chunk)
        return chunk

    def readinto(self, buffer) -> int:
        chunk = self.inner.read(len(buffer))
        buffer[: len(chunk)] = chunk
        self.count += len(chunk)
        return len(chunk)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.inner.seek(offset, whence)

    def tell(self) -> int:
        return self.inner.tell()

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True


def counting_source(path: Path):
    """``(source, counters)``: a source whose bytes are counted as they are read."""
    counters: list = []

    def opener():
        counter = ReadCounter(open(path, "rb"))
        counters.append(counter)
        return counter

    source = make_source(path)
    counted = type(source)(
        name=source.name,
        relative_path=source.relative_path,
        size=source.size,
        exists=source.exists,
        _open_binary=opener,
        _digest=lambda: "0" * 64,
    )
    return counted, counters
