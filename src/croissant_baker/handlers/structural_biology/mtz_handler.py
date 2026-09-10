"""MTZ handler: the columns of a reflection file, and the crystal behind them.

An MTZ keeps its header at the end, and the first words say where. So the
reader seeks straight to it with :mod:`struct` and reads 80-character records;
the reflection data between the preamble and the header is never touched.
"""

from __future__ import annotations

import logging
import math
import struct

import mlcroissant as mlc

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.utils import (
    BASE,
    allocate_record_set_ids,
    display_name,
    make_field_id,
)
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

#: Unregistered with IANA. The ``x-`` form follows ``application/x-nifti``,
#: already in the tree.
MIME_TYPE = "application/x-mtz"

SIGNATURE = b"MTZ "

#: Words 1 to 3: the signature, the header's 1-based word offset, and the
#: machine stamp whose first byte's high nibble names the number format.
PREAMBLE_BYTES = 12
_LITTLE_NIBBLE = 4
_BIG_NIBBLE = 1

#: Header records are fixed-width and space padded, and END closes the list.
RECORD_BYTES = 80
END = "END"

#: Reading past the header is reading reflection data, so it is bounded.
MAX_RECORDS = 100_000

#: An index, a plain integer, a batch number and an M/ISYM word all count
#: things. Every other MTZ column type is a measurement.
INTEGER_TYPES = frozenset("HIBY")
FLOAT_TYPES = frozenset("FQJDPWAGLKMER")

INTEGER = "sc:Integer"
FLOAT = "sc:Float"


def column_data_type(letter: str) -> str:
    """The Croissant type for one MTZ column type letter.

    An unknown letter is a measurement rather than a refusal: MTZ gains column
    types over time, and a float is what the file stores either way.
    """
    if letter in INTEGER_TYPES:
        return INTEGER
    if letter not in FLOAT_TYPES:
        logger.debug("Unknown MTZ column type %r; describing it as a float", letter)
    return FLOAT


def _endianness(stamp: bytes) -> str:
    """Little or big, from the high nibble of the machine stamp's first byte."""
    nibble = stamp[0] >> 4 if stamp else 0
    if nibble == _BIG_NIBBLE:
        return ">"
    if nibble != _LITTLE_NIBBLE:
        logger.debug("Unrecognised MTZ machine stamp %s; reading little-endian", stamp)
    return "<"


def _header_records(stream, name: str) -> list:
    """The header's 80-character records, up to and excluding ``END``.

    The offset in word 2 is why an MTZ can be described without reading the
    reflections: it points past all of them.
    """
    preamble = stream.read(PREAMBLE_BYTES)
    if len(preamble) < PREAMBLE_BYTES or preamble[:4] != SIGNATURE:
        raise ValueError(f"Not an MTZ file: {name} carries no 'MTZ ' header")

    order = _endianness(preamble[8:12])
    (word,) = struct.unpack(f"{order}i", preamble[4:8])
    if word < 1:
        raise ValueError(f"MTZ header offset {word} in {name} points at no header")

    stream.seek((word - 1) * 4)
    records = []
    for _ in range(MAX_RECORDS):
        raw = stream.read(RECORD_BYTES)
        if len(raw) < RECORD_BYTES:
            break
        record = raw.decode("ascii", "replace").rstrip()
        if record.startswith(END):
            break
        records.append(record)

    if not records:
        raise ValueError(
            f"MTZ header not found in {name}: word 2 points at byte "
            f"{(word - 1) * 4}, where no header record begins"
        )
    return records


def _quoted(record: str) -> str:
    """The single-quoted field of a SYMINF record, which may hold spaces."""
    first = record.find("'")
    last = record.rfind("'")
    return record[first + 1 : last] if 0 <= first < last else ""


def _resolution(low_word: float, high_word: float) -> dict:
    """RESO carries 1/d^2, so a distance in Angstrom is 1/sqrt of it.

    Zero is what a writer leaves when it computed no range, and inverting it
    would invent an infinite resolution.
    """
    out = {}
    for key, value in (
        ("resolution_low_angstrom", low_word),
        ("resolution_high_angstrom", high_word),
    ):
        if value > 0:
            out[key] = 1.0 / math.sqrt(value)
    return out


def _dataset(datasets: dict, ident: int) -> dict:
    """The entry for one dataset id, created on first mention.

    PROJECT, CRYSTAL, DATASET and DWAVEL arrive as four separate records for
    the same id, and any of them may be the first this file carries.
    """
    return datasets.setdefault(ident, {"id": ident})


def _read_mtz_properties(records: list, name: str) -> dict:
    """Everything the header records say about the file and its crystal."""
    props: dict = {}
    columns = []
    datasets: dict = {}

    for record in records:
        parts = record.split()
        if not parts:
            continue
        keyword, rest = parts[0], parts[1:]
        try:
            if keyword == "VERS" and rest:
                props["version"] = record[len(keyword) :].strip()
            elif keyword == "TITLE":
                props["title"] = record[len(keyword) :].strip()
            elif keyword == "NCOL" and len(rest) >= 3:
                props["n_columns"] = int(rest[0])
                props["n_reflections"] = int(rest[1])
                props["n_batches"] = int(rest[2])
            elif keyword == "CELL" and len(rest) >= 6:
                props["unit_cell"] = tuple(float(value) for value in rest[:6])
            elif keyword == "SYMINF" and len(rest) >= 4:
                props["space_group_number"] = int(rest[3])
                props["space_group"] = _quoted(record)
            elif keyword == "RESO" and len(rest) >= 2:
                props.update(_resolution(float(rest[0]), float(rest[1])))
            elif keyword == "COLUMN" and len(rest) >= 5:
                columns.append((rest[0], rest[1], int(rest[-1])))
            elif keyword in ("PROJECT", "CRYSTAL", "DATASET") and len(rest) >= 2:
                entry = _dataset(datasets, int(rest[0]))
                entry[
                    {"PROJECT": "project", "CRYSTAL": "crystal", "DATASET": "name"}[
                        keyword
                    ]
                ] = " ".join(rest[1:])
            elif keyword == "DWAVEL" and len(rest) >= 2:
                _dataset(datasets, int(rest[0]))["wavelength"] = float(rest[1])
        except ValueError as exc:
            # One malformed record costs its own field, not the file: the
            # columns are what a consumer came for.
            logger.debug("Skipping MTZ header record %r in %s: %s", record, name, exc)

    if not columns:
        raise ValueError(f"MTZ file {name} declares no COLUMN in its header")

    props["columns"] = columns
    props["datasets"] = [datasets[key] for key in sorted(datasets)]
    return props


class MTZHandler(FileTypeHandler):
    """Handler for MTZ reflection files (``.mtz``).

    One record set per file, one field per reflection column, typed from the
    column's MTZ type letter. Fields carry ``source: {fileObject: …}`` and no
    ``extract``: mlcroissant's reader dispatches on ``encodingFormat`` over a
    fixed list MTZ is not on, so an ``extract`` here would promise a read
    nobody can perform.
    """

    EXTENSIONS = (".mtz",)
    FORMAT_NAME = "MTZ"
    FORMAT_DESCRIPTION = (
        "Reflection column labels and types, unit cell, space group, "
        "resolution range, datasets"
    )

    def claims(self, source: FileSource) -> bool:
        """The suffix and the signature both, because ``.mtz`` is claimed by
        nothing else and a file that fails the signature is not one."""
        return source.suffix == ".mtz" and source.peek(4) == SIGNATURE

    def extract(self, source: FileSource, **kwargs) -> dict:
        if not source.exists:
            raise FileNotFoundError(f"MTZ file not found: {source.relative_path}")

        name = str(source.relative_path)
        try:
            with source.open() as stream:
                records = _header_records(stream, name)
        except OSError as exc:
            raise ValueError(f"Failed to read MTZ file {name}: {exc}") from exc

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": MIME_TYPE,
            "mtz_properties": _read_mtz_properties(records, name),
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """One record set per file. No FileSet: one MTZ is one table, and a
        set spanning two would claim they share a column list."""
        if not file_metas:
            return BuildResult([], [])

        allocated = allocate_record_set_ids(file_metas, [], include_base=True)
        record_sets = [
            _record_set(ids[BASE], meta, file_id)
            for meta, file_id, ids in zip(file_metas, file_ids, allocated)
        ]
        return BuildResult([], record_sets)


def _record_set(rs_id: str, meta: dict, file_id: str) -> mlc.RecordSet:
    """One file's reflections: a row per reflection, a field per column."""
    props = meta["mtz_properties"]
    names = {entry["id"]: entry.get("name") for entry in props.get("datasets", [])}

    used: set = set()
    fields = [
        mlc.Field(
            id=make_field_id(rs_id, label, used),
            name=label,
            description=_column_description(label, letter, names.get(dataset_id)),
            data_types=[column_data_type(letter)],
            source=mlc.Source(file_object=file_id),
        )
        for label, letter, dataset_id in props["columns"]
    ]
    return mlc.RecordSet(
        id=rs_id,
        name=rs_id,
        description=_description(meta, props),
        fields=fields,
    )


def _column_description(label: str, letter: str, dataset: str) -> str:
    """The column's own label and MTZ type letter, and where it came from."""
    origin = f" of dataset '{dataset}'" if dataset else ""
    return f"Reflection column '{label}' (MTZ type {letter}){origin}"


def _description(meta: dict, props: dict) -> str:
    """What this file holds, in the order a crystallographer would ask."""
    parts = (
        [f"{props['n_reflections']} reflections"] if "n_reflections" in props else []
    )
    parts.append(f"{len(props['columns'])} columns")

    if props.get("space_group"):
        parts.append(f"space group {props['space_group']}")
    cell = props.get("unit_cell")
    if cell:
        parts.append("cell " + ", ".join(f"{value:g}" for value in cell))

    low = props.get("resolution_low_angstrom")
    high = props.get("resolution_high_angstrom")
    if low is not None and high is not None:
        parts.append(f"resolution {low:g} to {high:g} Angstrom")
    elif high is not None:
        parts.append(f"resolution to {high:g} Angstrom")

    datasets = [
        entry["name"] for entry in props.get("datasets", []) if entry.get("name")
    ]
    if datasets:
        parts.append("datasets " + ", ".join(datasets))

    return f"Reflection data in {display_name(meta)} ({'; '.join(parts)})."
