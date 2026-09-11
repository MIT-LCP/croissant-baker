"""SerialEM mdoc handler: the keys an acquisition records per image.

An mdoc sits beside the stack it describes and carries the acquisition
metadata the image format has nowhere to put: tilt angle, stage position,
dose, and the frame file each image came from. The schema is the union of the
keys its sections declare, which is what becomes a record set here.

The grammar is small enough to live in this module: ``Key = value`` lines, and
bracketed section headers that split them into one group per image.
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

#: SerialEM's own extension has no IANA registration. The ``x-`` form follows
#: ``text/x-geo-soft``, already in the tree.
ENCODING_FORMAT = "text/x-mdoc"

#: Said on every record set: a consumer reading one in isolation cannot
#: otherwise tell that the fields carry no data.
NO_VALUE_NOTICE = "No value is emitted."

#: The globals worth repeating in prose: what the sections belong to, how big a
#: pixel is, and at what voltage the microscope ran.
HIGHLIGHTED_GLOBALS = (
    ("ImageFile", "image file"),
    ("PixelSpacing", "pixel spacing"),
    ("Voltage", "voltage"),
)

#: ``[ZValue = 3]``, ``[MontSection = 1]``, ``[FrameSet = 0]``. The key names
#: what a section is; ``[T = ...]`` is a title line and matches nothing here.
_SECTION = re.compile(r"\[\s*(?P<kind>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*.*?\s*\]")

#: The title SerialEM stamps at the head of a file, and never a section.
_TITLE = re.compile(r"\[\s*T\s*=\s*(?P<title>.*?)\s*\]")

_INTEGER = re.compile(r"[+-]?\d+\Z")
_FLOAT = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?\Z")

INTEGER_TYPE = "sc:Integer"
FLOAT_TYPE = "sc:Float"
TEXT_TYPE = "sc:Text"


@dataclass(frozen=True)
class MdocFile:
    """One mdoc, as far as its schema goes.

    Attributes:
        globals: Keys declared before the first section, in file order.
        section_kind: What the section headers call themselves, ``ZValue`` for
            a tilt series, ``None`` when the file declares no section.
        n_sections: How many sections the file declares.
        section_keys: ``(name, croissant_type)`` per key any section declares,
            in first-seen order. The union, because SerialEM stops writing a
            key when the feature producing it is off.
        titles: The ``[T = ...]`` lines, in file order.
    """

    globals: Dict[str, str]
    section_kind: Optional[str]
    n_sections: int
    section_keys: Tuple[Tuple[str, str], ...]
    titles: Tuple[str, ...]


def _value_kind(value: str) -> str:
    """Whether one written value reads as an integer, a decimal, or neither.

    A value holding several whitespace-separated numbers is neither: Croissant
    types one value per field, and ``12.5 -3.1`` is a pair.
    """
    text = value.strip()
    if not text or (_INTEGER.match(text) is None and _FLOAT.match(text) is None):
        return "text"
    return "int" if _INTEGER.match(text) else "float"


def _croissant_type(values: List[str]) -> str:
    """The type a key's values across the whole file agree on.

    Widest wins: SerialEM writes ``0`` where it means ``0.0``, so a key that is
    an integer in one section and a decimal in the next is a decimal.
    """
    kinds = {_value_kind(value) for value in values}
    if kinds == {"int"}:
        return INTEGER_TYPE
    if kinds and kinds <= {"int", "float"}:
        return FLOAT_TYPE
    return TEXT_TYPE


def parse(lines) -> MdocFile:
    """Read one mdoc forwards, line by line, keeping names and types only."""
    globals_: Dict[str, str] = {}
    titles: List[str] = []
    section_kind: Optional[str] = None
    n_sections = 0
    values: Dict[str, List[str]] = {}

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("["):
            title = _TITLE.fullmatch(line)
            if title:
                titles.append(title.group("title"))
                continue
            section = _SECTION.fullmatch(line)
            if section:
                n_sections += 1
                if section_kind is None:
                    section_kind = section.group("kind")
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        if n_sections:
            values.setdefault(key, []).append(value.strip())
        else:
            globals_[key] = value.strip()

    return MdocFile(
        globals=globals_,
        section_kind=section_kind,
        n_sections=n_sections,
        section_keys=tuple(
            (key, _croissant_type(seen)) for key, seen in values.items()
        ),
        titles=tuple(titles),
    )


class MdocHandler(FileTypeHandler):
    """Handler for SerialEM acquisition metadata (``.mdoc``).

    One record set per file, whose fields are the keys the sections declare,
    typed from the values across every section. A file that declares no section
    is described by its global keys instead, as the one row it is.

    Fields carry ``source: {fileObject: …}`` and no ``extract``: mlcroissant
    dispatches its reader on ``encodingFormat`` over a fixed list mdoc is not
    on, so an ``extract`` here would be a promise nobody can keep.
    """

    EXTENSIONS = (".mdoc",)
    FORMAT_NAME = "SerialEM mdoc"
    FORMAT_DESCRIPTION = (
        "Acquisition globals and the per-section keys of a tilt series or montage"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim any ``.mdoc``, wrapped or not: ``source.suffix`` is logical."""
        return source.suffix == ".mdoc"

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Read one mdoc in a single forward pass."""
        if not source.exists:
            raise FileNotFoundError(f"mdoc file not found: {source.relative_path}")

        try:
            with source.open_text() as stream:
                parsed = parse(stream)
        except Exception as exc:  # noqa: BLE001, one file's failure, named
            # Undecodable bytes raise from the text wrapper, and the reason a
            # user reads has to name the file.
            raise ValueError(
                f"Failed to read mdoc file {source.relative_path}: {exc}"
            ) from exc

        if not parsed.globals and not parsed.n_sections:
            raise ValueError(
                f"Not a SerialEM mdoc file: {source.relative_path} declares "
                "neither a 'Key = value' line nor a section"
            )

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": ENCODING_FORMAT,
            "mdoc": parsed,
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """One record set per file: the section schema, or the globals.

        No FileSet: two mdocs describe two acquisitions, and a FileSet spanning
        them would claim they share a schema.
        """
        if not file_metas:
            return BuildResult([], [])

        allocated = allocate_record_set_ids(file_metas, [], include_base=True)

        record_sets: List[mlc.RecordSet] = []
        for meta, file_id, ids in zip(file_metas, file_ids, allocated):
            built = _record_set(ids[BASE], meta, file_id)
            if built is not None:
                record_sets.append(built)
        return BuildResult([], record_sets)


def _record_set(rs_id: str, meta: dict, file_id: str) -> Optional[mlc.RecordSet]:
    parsed: MdocFile = meta["mdoc"]
    stored = display_name(meta)

    if parsed.section_keys:
        keys = parsed.section_keys
        label = "Section key"
    else:
        # A file with no section, or with sections that declare no key, still
        # states what it was acquired with. That is one row.
        keys = tuple(
            (key, _croissant_type([value])) for key, value in parsed.globals.items()
        )
        label = "Global key"

    if not keys:
        logger.warning(
            "%s declares no key any record set could describe; not described", stored
        )
        return None

    used: set = set()
    return mlc.RecordSet(
        id=rs_id,
        name=rs_id,
        description=_description(parsed, stored),
        fields=[
            mlc.Field(
                id=make_field_id(rs_id, key, used),
                name=key,
                description=f"{label} {key} in {stored}",
                data_types=[data_type],
                source=mlc.Source(file_object=file_id),
            )
            for key, data_type in keys
        ],
    )


def _description(parsed: MdocFile, stored: str) -> str:
    """What the record set says it is: the file, its sections, its globals."""
    if parsed.section_keys:
        rows = (
            f"{parsed.n_sections} {parsed.section_kind} "
            f"section{'' if parsed.n_sections == 1 else 's'}, "
            f"{len(parsed.section_keys)} "
            f"key{'' if len(parsed.section_keys) == 1 else 's'}"
        )
    else:
        rows = "one row, the acquisition's global keys"

    highlights = [
        f"{noun} {parsed.globals[key]}"
        for key, noun in HIGHLIGHTED_GLOBALS
        if parsed.globals.get(key)
    ]
    prefix = f"SerialEM acquisition metadata in {stored} ({rows})."
    if highlights:
        prefix += " Declared " + ", ".join(highlights) + "."
    if parsed.titles:
        prefix += f" Title: {parsed.titles[0]}."
    return f"{prefix} {NO_VALUE_NOTICE}"
