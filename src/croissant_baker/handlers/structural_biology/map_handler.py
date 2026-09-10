"""MRC / CCP4 map handler: the grid a density or micrograph file holds.

The 1024-byte header is a fixed record of 56 words, so :mod:`struct` reads it
and nothing else is opened. The data that follows is gigabytes of voxels and
describes nothing the header does not already say.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

import mlcroissant as mlc

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

#: Unregistered with IANA. The ``x-`` form follows ``application/x-nifti``,
#: already in the tree.
MIME_TYPE = "application/x-mrc"

#: One FileSet and one RecordSet over the whole batch, so the ids are fixed.
FILE_SET_ID = "mrc-files"
RECORD_SET_ID = "mrc_maps"


#: MRC2014 and CCP4 share one header, so one parser serves both.
HEADER_BYTES = 1024

#: Word 53 carries the format signature, at a byte offset the peek must reach.
SIGNATURE_OFFSET = 208
SIGNATURE = b"MAP "
PEEK_BYTES = SIGNATURE_OFFSET + 4

#: Word 54. A little-endian writer stamps 0x44 0x44 (or 0x44 0x41, which some
#: writers use for the same thing); a big-endian one stamps 0x11 0x11.
_LITTLE_STAMPS = (b"\x44\x44", b"\x44\x41")
_BIG_STAMPS = (b"\x11\x11",)

#: Word 4. Anything else is a mode this handler cannot name, and naming the
#: stored type wrongly is worse than refusing the file.
MODE_DTYPES = {
    0: "int8",
    1: "int16",
    2: "float32",
    3: "complex int16",
    4: "complex float32",
    6: "uint16",
    12: "float16",
    101: "4-bit unsigned",
}

#: Word 23. 0 is a stack of 2D images, 1..230 is a crystallographic space
#: group over one volume, and 401..630 marks a stack of volumes.
IMAGE_STACK = "image stack"
VOLUME = "volume"
VOLUME_STACK = "volume stack"
_VOLUME_STACK_RANGE = range(401, 631)


def _endianness(stamp: bytes) -> str:
    """The byte order the file was written in, from its machine stamp.

    An unrecognised stamp falls back to little-endian: it is what current
    hardware writes, and the dimensions that follow are checked anyway.
    """
    if stamp[:2] in _BIG_STAMPS:
        return ">"
    if stamp[:2] not in _LITTLE_STAMPS:
        logger.debug("Unrecognised MRC machine stamp %s; reading little-endian", stamp)
    return "<"


def _kind(ispg: int) -> str:
    """What the third dimension means, from the space-group word."""
    if ispg == 0:
        return IMAGE_STACK
    if ispg in _VOLUME_STACK_RANGE:
        return VOLUME_STACK
    return VOLUME


def _read_mrc_properties(header: bytes, name: str) -> dict:
    """Everything the 56 header words say about the grid."""
    if len(header) < HEADER_BYTES:
        raise ValueError(
            f"Not an MRC map: {name} is {len(header)} bytes, short of the "
            f"{HEADER_BYTES}-byte header"
        )

    order = _endianness(header[212:216])
    nx, ny, nz, mode = struct.unpack(f"{order}4i", header[0:16])
    mx, my, mz = struct.unpack(f"{order}3i", header[28:40])
    cell_a, cell_b, cell_c = struct.unpack(f"{order}3f", header[40:52])
    dmin, dmax, dmean = struct.unpack(f"{order}3f", header[76:88])
    (ispg,) = struct.unpack(f"{order}i", header[88:92])
    (nversion,) = struct.unpack(f"{order}i", header[108:112])
    (nlabl,) = struct.unpack(f"{order}i", header[220:224])

    if min(nx, ny, nz) <= 0:
        raise ValueError(
            f"Not an MRC map: {name} declares a grid of {nx} by {ny} by {nz}"
        )
    if mode not in MODE_DTYPES:
        raise ValueError(f"Unknown MRC mode {mode} in {name}")

    kind = _kind(ispg)
    props: dict = {
        "dim_x": nx,
        "dim_y": ny,
        "dim_z": nz,
        "mode": mode,
        "data_dtype": MODE_DTYPES[mode],
        "space_group": ispg,
        "kind": kind,
    }

    # Sampling can be zero in a file that carries no cell, and a voxel size
    # divided out of it would be an invented number.
    for axis, cell, sampling in (
        ("x", cell_a, mx),
        ("y", cell_b, my),
        ("z", cell_c, mz),
    ):
        if sampling > 0:
            props[f"voxel_size_{axis}"] = cell / sampling

    if kind == IMAGE_STACK:
        props["n_images"] = nz
    elif kind == VOLUME_STACK:
        # A volume stack packs its volumes along z, mz slices each.
        props["n_images"] = nz // mz if mz > 0 else nz

    if nversion:
        props["nversion"] = nversion

    labels = _labels(header, nlabl)
    props["n_labels"] = len(labels)
    if labels:
        props["first_label"] = labels[0]

    props["density_min"] = float(dmin)
    props["density_max"] = float(dmax)
    props["density_mean"] = float(dmean)
    return props


def _labels(header: bytes, nlabl: int) -> list:
    """The text labels a writer left, up to the ten the header holds."""
    count = max(0, min(nlabl, 10))
    out = []
    for i in range(count):
        start = 224 + i * 80
        text = header[start : start + 80].decode("ascii", "replace").strip()
        if text:
            out.append(text)
    return out


class MRCHandler(FileTypeHandler):
    """Handler for MRC2014 and CCP4 maps (``.mrc``, ``.mrcs``, ``.map``,
    ``.ccp4``).

    Reads the fixed 1024-byte header and stops there: grid size, stored data
    type, voxel size, space group and whether the file is one volume or a stack.
    """

    EXTENSIONS = (".mrc", ".mrcs", ".map", ".ccp4")
    FORMAT_NAME = "MRC / CCP4 map"
    FORMAT_DESCRIPTION = (
        "Grid dimensions, data type, voxel size, space group, volume or stack"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim a declared suffix, and ask ``.map`` for the signature too.

        ``.map`` is generic enough to name anything, so word 53 has to settle
        it. The other three suffixes are claimed on the extension alone: an
        older CCP4 writer may leave the signature out, and a file this handler
        cannot read is better reported as unreadable than as unclaimed.
        """
        if source.suffix not in self.EXTENSIONS:
            return False
        if source.suffix != ".map":
            return True
        return source.peek(PEEK_BYTES)[SIGNATURE_OFFSET:PEEK_BYTES] == SIGNATURE

    def extract(self, source: FileSource, **kwargs) -> dict:
        if not source.exists:
            raise FileNotFoundError(f"MRC map not found: {source.relative_path}")

        try:
            with source.open() as stream:
                header = stream.read(HEADER_BYTES)
        except OSError as exc:
            raise ValueError(
                f"Failed to read MRC map {source.relative_path}: {exc}"
            ) from exc

        props = _read_mrc_properties(header, str(source.relative_path))

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": MIME_TYPE,
            "mrc_properties": props,
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """One FileSet over the batch, and one record per file in it.

        Every map in a batch carries the same header, so the fields describe
        each file rather than each voxel, and a single FileSet is what they
        read from.
        """
        # A FileSet built over no file would promise data that is not there.
        if not file_metas:
            return BuildResult([], [])

        properties = [meta["mrc_properties"] for meta in file_metas]
        count = len(file_metas)
        dims = _dims_note(properties)
        kinds = _counted(props["kind"] for props in properties)
        dtypes = _counted(props["data_dtype"] for props in properties)
        stacked = any(props.get("n_images") is not None for props in properties)

        file_set = mlc.FileSet(
            id=FILE_SET_ID,
            name="MRC / CCP4 maps",
            description=f"{count} MRC/CCP4 map file(s) ({dims}; {kinds})",
            encoding_formats=sorted({meta["encoding_format"] for meta in file_metas}),
            includes=_includes(file_metas),
        )

        record_set = mlc.RecordSet(
            id=RECORD_SET_ID,
            name=RECORD_SET_ID,
            description=(
                f"{count} MRC/CCP4 map file(s) ({dims}): {kinds}; stored as {dtypes}"
            ),
            fields=_fields(stacked, dtypes, kinds),
        )
        return BuildResult([file_set], [record_set])


def _includes(file_metas: list) -> list:
    """A glob per suffix the batch actually carries, in declared order."""
    present = {Path(meta["file_name"]).suffix.lower() for meta in file_metas}
    return [f"**/*{ext}" for ext in MRCHandler.EXTENSIONS if ext in present]


def _dims_note(properties: list) -> str:
    """``128x128x64`` for one shape, ``64-128x128x64-200`` for a mixed batch."""
    parts = []
    for key in ("dim_x", "dim_y", "dim_z"):
        values = [props[key] for props in properties if key in props]
        if not values:
            continue
        low, high = min(values), max(values)
        parts.append(f"{low}" if low == high else f"{low}-{high}")
    return "x".join(parts) if parts else "unknown dims"


def _counted(values) -> str:
    """``volume, image stack``, in first-seen order and without repeats."""
    seen = dict.fromkeys(values)
    return ", ".join(seen) if seen else "unknown"


def _fields(stacked: bool, dtypes: str, kinds: str) -> list:
    """The per-file record, in the order the header states it."""
    described = [
        ("dim_x", "sc:Integer", "MRC word 1 (nx); grid columns"),
        ("dim_y", "sc:Integer", "MRC word 2 (ny); grid rows"),
        ("dim_z", "sc:Integer", "MRC word 3 (nz); grid sections"),
        ("data_dtype", "sc:Text", f"MRC word 4 (mode); stored data type ({dtypes})"),
        (
            "voxel_size",
            "sc:Text",
            "MRC cella divided by mx, my, mz; x, y, z in Angstrom",
        ),
        (
            "space_group",
            "sc:Integer",
            "MRC word 23 (ispg); 0 for an image stack, otherwise the space group",
        ),
        ("kind", "sc:Text", f"What the file holds ({kinds})"),
    ]
    if stacked:
        described.append(
            ("n_images", "sc:Integer", "Images or volumes stacked along the z axis")
        )
    return [
        mlc.Field(
            id=f"{RECORD_SET_ID}/{name}",
            name=name,
            description=description,
            data_types=[data_type],
            source=mlc.Source(
                file_set=FILE_SET_ID,
                extract=mlc.Extract(file_property="content"),
            ),
        )
        for name, data_type, description in described
    ]
