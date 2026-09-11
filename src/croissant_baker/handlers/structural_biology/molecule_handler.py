"""Small-molecule handler: what a compound library declares about its records.

An SDF is a catalogue: molecule after molecule, each with the property tags the
depositor attached, and those tags are the columns anyone querying the library
will ask for. MOL is the same record on its own, and Tripos MOL2 is the docking
world's equivalent. All three are read here with the standard library, forwards
and once, keeping names and types and never a coordinate.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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

#: The conventional ``chemical/*`` types. None is IANA-registered, and none
#: makes these record sets readable by mlcroissant, so they are documentation.
ENCODING_FORMATS = {
    ".sdf": "chemical/x-mdl-sdfile",
    ".mol": "chemical/x-mdl-molfile",
    ".mol2": "chemical/x-mol2",
}

#: What prose calls each format.
FORMAT_LABELS = {
    ".sdf": "MDL SDF",
    ".mol": "MDL molfile",
    ".mol2": "Tripos MOL2",
}

#: Said on every record set: a consumer reading one in isolation cannot
#: otherwise tell that the fields carry no data.
NO_VALUE_NOTICE = "No value is emitted."

#: The end of one record's connection table.
CTAB_END = "M  END"
#: The end of one record in a multi-molecule file.
RECORD_END = "$$$$"
#: Where a V3000 record keeps the counts its counts line left at zero.
V3000_COUNTS = "M  V30 COUNTS"
#: What starts a MOL2 record. Its next four lines are name, counts, type, charge.
MOL2_MOLECULE = "@<TRIPOS>MOLECULE"

#: The molecule types Tripos defines. The line holding one is free text in a
#: format anyone can write, so a value outside the vocabulary is counted under
#: :data:`OTHER_KIND` rather than kept: a description built from what the file
#: happened to say there is bounded by the file's size, not by the format's.
MOL2_TYPES = frozenset({"SMALL", "BIOPOLYMER", "PROTEIN", "NUCLEIC_ACID", "SACCHARIDE"})
OTHER_KIND = "other"

_INTEGER = re.compile(r"[+-]?\d+\Z")
_FLOAT = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?\Z")
#: ``> <MW>``, ``>  <MW>  (1)``: the tag is what the first angle brackets hold.
_TAG = re.compile(r">.*?<(?P<tag>[^>]*)>")

INTEGER_TYPE = "sc:Integer"
FLOAT_TYPE = "sc:Float"
TEXT_TYPE = "sc:Text"


@dataclass(frozen=True)
class Molecules:
    """One small-molecule file, as far as its schema goes.

    Attributes:
        format: What prose calls the format, from :data:`FORMAT_LABELS`.
        n_molecules: How many records the file holds.
        atom_range: ``(min, max)`` atoms per record, ``None`` when no record
            declared a count.
        bond_range: ``(min, max)`` bonds per record, or ``None``.
        kinds: The connection-table versions seen (``V2000``, ``V3000``), or
            for a MOL2 the Tripos molecule types of :data:`MOL2_TYPES`, with
            anything else as :data:`OTHER_KIND`. In first-seen order.
        tags: ``(name, croissant_type)`` per SDF property tag, in first-seen
            order. Empty for MOL2, which has no equivalent.
        named: Whether any record declares a molecule name.
    """

    format: str
    n_molecules: int
    atom_range: Optional[Tuple[int, int]]
    bond_range: Optional[Tuple[int, int]]
    kinds: Tuple[str, ...]
    tags: Tuple[Tuple[str, str], ...]
    named: bool


def _value_kind(value: List[str]) -> str:
    """Whether one tag value reads as an integer, a decimal, or neither.

    A value running over several lines is neither: Croissant types one value
    per field, and two lines are prose however they are spelled.
    """
    if len(value) != 1:
        return "text"
    text = value[0].strip()
    if not text or (_INTEGER.match(text) is None and _FLOAT.match(text) is None):
        return "text"
    return "int" if _INTEGER.match(text) else "float"


#: Widening order. An integer column can turn out to be decimal and a decimal
#: one to be text, and neither goes back, so one kind per tag says everything a
#: list of every value it was ever written with would.
_KINDS = ("int", "float", "text")

_CROISSANT_TYPES = {"int": INTEGER_TYPE, "float": FLOAT_TYPE, "text": TEXT_TYPE}


def _widen(current: Optional[str], kind: str) -> str:
    """The kind covering both, so a library of a million records costs one
    string per tag rather than one value per record."""
    if current is None:
        return kind
    return _KINDS[max(_KINDS.index(current), _KINDS.index(kind))]


def _croissant_type(kind: Optional[str]) -> str:
    """The Croissant type for a tag's widened kind.

    Widest wins: a tag written ``0`` in one record and ``0.5`` in the next is a
    decimal, and a tag nobody filled in is text.
    """
    return TEXT_TYPE if kind is None else _CROISSANT_TYPES[kind]


def _range(counts: List[int]) -> Optional[Tuple[int, int]]:
    return (min(counts), max(counts)) if counts else None


def _counts(line: str) -> Optional[Tuple[int, int]]:
    """Atoms and bonds off a V2000 counts line, whose columns are fixed width.

    ``  6  6  0  0  0  0            999 V2000``: atoms in columns 1 to 3, bonds
    in 4 to 6. Splitting on whitespace instead would misread a library whose
    three-digit counts run together.
    """
    try:
        return int(line[0:3]), int(line[3:6])
    except ValueError:
        return None


def _ordered(seen: List[str]) -> Tuple[str, ...]:
    """First-seen order, without repeats. ``dict`` is the ordered set here."""
    return tuple(dict.fromkeys(seen))


class _Record:
    """One MDL record being read, and only what survives it."""

    def __init__(self) -> None:
        self.index = 0
        self.name = ""
        self.atoms: Optional[int] = None
        self.bonds: Optional[int] = None
        self.version: Optional[str] = None
        self.ctab_ended = False
        self.tag: Optional[str] = None
        self.value: List[str] = []
        self.values: Dict[str, List[str]] = {}

    @property
    def declared(self) -> bool:
        """Whether this is a record at all, rather than trailing whitespace."""
        return self.version is not None or self.ctab_ended

    def close_tag(self) -> None:
        if self.tag is not None:
            self.values.setdefault(self.tag, self.value)
            self.tag = None
            self.value = []


def parse_mdl(lines, label: str) -> Molecules:
    """Read an SDF or MOL forwards, one line at a time.

    Records are separated by ``$$$$``; a lone molfile has no terminator, so the
    last record is closed at end of file.
    """
    n_molecules = 0
    atoms: List[int] = []
    bonds: List[int] = []
    versions: List[str] = []
    named = False
    tag_kinds: Dict[str, str] = {}
    record = _Record()

    def close(record: _Record) -> None:
        nonlocal n_molecules, named
        record.close_tag()
        if not record.declared:
            return
        n_molecules += 1
        named = named or bool(record.name)
        if record.atoms is not None:
            atoms.append(record.atoms)
        if record.bonds is not None:
            bonds.append(record.bonds)
        if record.version:
            versions.append(record.version)
        for tag, value in record.values.items():
            tag_kinds[tag] = _widen(tag_kinds.get(tag), _value_kind(value))

    for raw in lines:
        line = raw.rstrip("\n").rstrip("\r")
        stripped = line.strip()

        if stripped == RECORD_END:
            close(record)
            record = _Record()
            continue

        if record.index == 0:
            record.name = stripped
        elif record.index == 3:
            counted = _counts(line)
            if counted is not None:
                record.atoms, record.bonds = counted
            if "V3000" in line:
                record.version = "V3000"
                # The counts line of a V3000 record is all zeroes; the CTAB
                # states the truth a few lines further down.
                record.atoms = record.bonds = None
            elif counted is not None or "V2000" in line:
                record.version = "V2000"
        elif stripped == CTAB_END:
            record.ctab_ended = True
        elif record.version == "V3000" and stripped.startswith(V3000_COUNTS):
            counted = stripped[len(V3000_COUNTS) :].split()
            if len(counted) >= 2 and all(_INTEGER.match(c) for c in counted[:2]):
                record.atoms, record.bonds = int(counted[0]), int(counted[1])
        elif record.ctab_ended:
            _read_data_line(record, line, stripped)

        record.index += 1

    close(record)

    return Molecules(
        format=label,
        n_molecules=n_molecules,
        atom_range=_range(atoms),
        bond_range=_range(bonds),
        kinds=_ordered(versions),
        tags=tuple((tag, _croissant_type(kind)) for tag, kind in tag_kinds.items()),
        named=named,
    )


def _read_data_line(record: _Record, line: str, stripped: str) -> None:
    """One line of the data block: a tag header, a value line, or a separator."""
    if stripped.startswith(">"):
        record.close_tag()
        found = _TAG.match(stripped)
        if found and found.group("tag"):
            record.tag = found.group("tag")
        return
    if not stripped:
        record.close_tag()
        return
    if record.tag is not None:
        record.value.append(line)


def parse_mol2(lines, label: str) -> Molecules:
    """Read a MOL2 forwards: each record states its counts in four fixed lines."""
    n_molecules = 0
    atoms: List[int] = []
    bonds: List[int] = []
    types: List[str] = []
    named = False
    header: Optional[int] = None

    for raw in lines:
        line = raw.strip()

        if line == MOL2_MOLECULE:
            n_molecules += 1
            header = 0
            continue
        if header is None:
            continue

        if header == 0:
            named = named or bool(line)
        elif header == 1:
            counted = line.split()
            if len(counted) >= 2 and all(_INTEGER.match(c) for c in counted[:2]):
                atoms.append(int(counted[0]))
                bonds.append(int(counted[1]))
        elif header == 2 and line:
            declared = line.upper()
            types.append(declared if declared in MOL2_TYPES else OTHER_KIND)

        header = header + 1 if header < 3 else None

    return Molecules(
        format=label,
        n_molecules=n_molecules,
        atom_range=_range(atoms),
        bond_range=_range(bonds),
        kinds=_ordered(types),
        tags=(),
        named=named,
    )


class SmallMoleculeHandler(FileTypeHandler):
    """Handler for small-molecule records (``.sdf``, ``.mol``, ``.mol2``).

    One record set per file: the molecule name, its atom and bond counts, and a
    field per SDF property tag, typed from the tag's values across the file.

    Fields carry ``source: {fileObject: …}`` and no ``extract``: mlcroissant
    dispatches its reader on ``encodingFormat`` over a fixed list none of these
    is on, so an ``extract`` here would be a promise nobody can keep.
    """

    EXTENSIONS = (".sdf", ".mol", ".mol2")
    FORMAT_NAME = "Small molecules"
    FORMAT_DESCRIPTION = (
        "Molecule and atom counts, and the property tags an SDF library declares"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim a declared extension, wrapped or not: the suffix is logical."""
        return source.suffix in self.EXTENSIONS

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Read one molecule file in a single forward pass."""
        if not source.exists:
            raise FileNotFoundError(f"Molecule file not found: {source.relative_path}")

        suffix = source.suffix
        label = FORMAT_LABELS[suffix]
        read = parse_mol2 if suffix == ".mol2" else parse_mdl

        try:
            with source.open_text() as stream:
                parsed = read(stream, label)
        except Exception as exc:  # noqa: BLE001, one file's failure, named
            # Undecodable bytes raise from the text wrapper, and the reason a
            # user reads has to name the file.
            raise ValueError(
                f"Failed to read {label} file {source.relative_path}: {exc}"
            ) from exc

        if not parsed.n_molecules:
            raise ValueError(
                f"Not a {label} file: {source.relative_path} declares no molecule"
            )

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": ENCODING_FORMATS[suffix],
            "molecules": parsed,
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """One record set per file, one row per molecule.

        No FileSet: two libraries are two catalogues, and a FileSet spanning
        them would claim they share a tag schema.
        """
        if not file_metas:
            return BuildResult([], [])

        allocated = allocate_record_set_ids(file_metas, [], include_base=True)

        record_sets = [
            _record_set(ids[BASE], meta, file_id)
            for meta, file_id, ids in zip(file_metas, file_ids, allocated)
        ]
        return BuildResult([], record_sets)


def _record_set(rs_id: str, meta: dict, file_id: str) -> mlc.RecordSet:
    parsed: Molecules = meta["molecules"]
    stored = display_name(meta)

    described = [
        ("name", TEXT_TYPE, f"Molecule name in {stored}"),
        ("n_atoms", INTEGER_TYPE, f"Atom count of one molecule in {stored}"),
        ("n_bonds", INTEGER_TYPE, f"Bond count of one molecule in {stored}"),
        *(
            (tag, data_type, f"Property tag {tag} in {stored}")
            for tag, data_type in parsed.tags
        ),
    ]

    used: set = set()
    return mlc.RecordSet(
        id=rs_id,
        name=rs_id,
        description=_description(parsed, stored),
        fields=[
            mlc.Field(
                id=make_field_id(rs_id, name, used),
                name=name,
                description=description,
                data_types=[data_type],
                source=mlc.Source(file_object=file_id),
            )
            for name, data_type, description in described
        ],
    )


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _spread(name: str, span: Optional[Tuple[int, int]]) -> str:
    """``6 atoms`` when every record agrees, ``6 to 9 atoms`` when they differ."""
    if span is None:
        return f"{name} count not declared"
    low, high = span
    return _plural(low, name) if low == high else f"{low} to {high} {name}s"


def _description(parsed: Molecules, stored: str) -> str:
    """What the record set says it is: the file, its format, what it holds."""
    parts = [
        _plural(parsed.n_molecules, "molecule"),
        _spread("atom", parsed.atom_range),
        _spread("bond", parsed.bond_range),
    ]
    if parsed.tags:
        parts.append(_plural(len(parsed.tags), "property tag"))
    text = f"{parsed.format} records in {stored} ({', '.join(parts)})."
    if parsed.kinds:
        text += " " + ", ".join(parsed.kinds) + "."
    if not parsed.named:
        text += " No molecule declares a name."
    return f"{text} {NO_VALUE_NOTICE}"
