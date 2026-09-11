"""CIF handler: the header of a crystallographic file, and no coordinate row.

A CIF is a syntax before it is a subject. One file holds one or more ``data_``
blocks, each a list of ``_name value`` items and ``loop_`` tables, and the same
grammar carries a protein deposit, a small-molecule structure, a powder pattern
and a dictionary. Two of those dialects are described here. PDBx/mmCIF is what
the wwPDB archive now treats as primary, and the only form that can hold a
structure too large for eighty columns; core CIF is what the COD, the CSD and
the IUCr journals ship a small molecule in. They are told apart by the
categories they use, not by their extension, which is the same for both.

Everything read is a fact the file states about itself: which entry it is, what
it was classified as, when it was deposited, how it was determined and at what
resolution, how many polymer entities and chains it holds; or, for a small
molecule, its name, its formula, its space group and its cell. Behind all of
that sits the ``_atom_site`` loop, which is the file: an archive entry is
megabytes of coordinates behind a header of a few kilobytes. So the read stops
at that loop, and a structure of a hundred thousand atoms costs the same read as
a fragment of three.

Nothing in front of the header says how long it is, so, as in PDB, what bounds
the read is that stop. A file that never reaches a coordinate table must still
stop somewhere, so the header is taken a chunk at a time and capped.

The tokenizer covers the CIF 1.1 subset the two dialects are written in, and no
more. What it does not do, deliberately, is listed on :func:`_tokenize`.

Depositors are not emitted. ``_audit_author`` names people and is bibliographic
rather than structural, and the dataset's own creator is a command-line input
rather than something read out of a file. The same reasoning as PDB.

No RecordSet: a structure is a file, and coordinate rows are not records of a
dataset schema. What this handler produces is a described FileObject, through
the ``description`` key the generator honours.
"""

import math
import re
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.utils import (
    MAX_HEADER_BYTES,
    PrefixLines,
    decode_line,
)
from croissant_baker.sources import UNREADABLE, FileSource

#: Neither dialect has an IANA registration. Both spellings come from the
#: chemical MIME family, which is where ``chemical/x-pdb`` comes from as well,
#: and they are what the archives and the molecular viewers use. Which one a
#: file gets is decided per file, by the dialect it turned out to be written in.
MMCIF_ENCODING_FORMAT = "chemical/x-mmcif"
CIF_ENCODING_FORMAT = "chemical/x-cif"

#: What the tokenizer emits: a block header, a loop introduction, an item name,
#: or a value. Values are what everything else is made of, so a token that is
#: none of the first three is one.
DATA, LOOP, NAME, VALUE = "data", "loop", "name", "value"

#: The reserved word that opens a block, and the one that opens a table. CIF
#: spells its reserved words case-insensitively, and files do vary.
DATA_PREFIX = "data_"
LOOP_WORD = "loop_"

#: The comment character, and the two interchangeable quoting characters.
COMMENT = "#"
QUOTES = "'\""

#: A text field runs from a line opening with this to the next line opening
#: with it. Only at the start of a physical line: anywhere else it is an
#: ordinary character.
TEXT_FIELD = ";"

#: The item names the coordinate table is written under, in each dialect. A
#: ``loop_`` whose first name begins with one of these is the coordinates, and
#: the read ends there.
ATOM_SITE_PREFIXES = ("_atom_site.", "_atom_site_")

#: ``.`` states that there is no value and ``?`` that the value is unknown.
#: Neither is a value, and reporting either as one would be inventing it.
NULL_VALUES = (".", "?")

#: The PDBx categories whose items are kept. Every other category is walked
#: through for its structure and nothing is stored, because a header carries
#: hundreds of them and this handler describes what is in this list.
PDBX_CATEGORIES = frozenset(
    {
        "_entry",
        "_audit_conform",
        "_pdbx_database_status",
        "_struct",
        "_struct_keywords",
        "_exptl",
        "_refine",
        "_em_3d_reconstruction",
        "_pdbx_nmr_ensemble",
        "_entity_poly",
        "_struct_asym",
    }
)

#: The core CIF item names kept. Core CIF has no category separator, so these
#: are whole names rather than prefixes, and they are held lower-cased because
#: CIF item names are case-insensitive and files spell them both ways.
CORE_ITEMS = frozenset(
    {
        "_chemical_name_common",
        "_chemical_name_systematic",
        "_chemical_formula_sum",
        "_cell_length_a",
        "_cell_length_b",
        "_cell_length_c",
        "_cell_angle_alpha",
        "_cell_angle_beta",
        "_cell_angle_gamma",
        "_space_group_name_h-m_alt",
        "_symmetry_space_group_name_h-m",
        "_diffrn_radiation_wavelength",
    }
)

#: The three items that say a block is PDBx. Any item of the ``_struct``
#: category says it too, which is why the test below is not just a membership.
PDBX_MARKERS = ("_entry.id", "_audit_conform.dict_name")

#: The two that say a block is a small molecule, once PDBx has been ruled out.
CORE_MARKERS = ("_cell_length_a", "_chemical_formula_sum")

#: The cell, as the description and the ``cell`` field both name its parts.
CELL_ITEMS = (
    ("a", "_cell_length_a"),
    ("b", "_cell_length_b"),
    ("c", "_cell_length_c"),
    ("alpha", "_cell_angle_alpha"),
    ("beta", "_cell_angle_beta"),
    ("gamma", "_cell_angle_gamma"),
)

#: The edges the description states, out of the six above.
CELL_EDGES = ("a", "b", "c")

#: The separator a CIF writes a list of keywords or of strand identifiers with.
VALUE_SEPARATOR = ","

#: A standard uncertainty, written as digits in parentheses on the end of a
#: number: ``10.1234(4)`` is one cell edge and a second value about it.
UNCERTAINTY = re.compile(r"\(\d+\)$")

#: The largest single line this handler will accumulate. A CIF line is usually
#: an item and its value, but a value can legitimately be long: an entry writes
#: ``_entity_poly.pdbx_seq_one_letter_code`` as an unwrapped one-letter sequence,
#: which for a large assembly runs to tens of kilobytes on one line. A megabyte
#: is far above any of those, and a line longer than that is not a line of a CIF
#: header: reading on for the end of it is reading the file this handler exists
#: not to read. Two caps rather than one, because the header cap does not bound
#: this: a reader assembling a line holds what it has read and re-copies it a
#: chunk at a time, so a file with no line ending in it costs the cap in memory
#: and the square of it in copying before the cap is reached.
MAX_LINE_BYTES = 1024 * 1024

#: How much of the head is read to decide a claim. A core CIF from the COD or
#: the CSD opens with a banner of comment lines, so the ``data_`` line is not
#: the first line of the file; a few kilobytes clears any banner anyone writes.
CLAIM_BYTES = 8 * 1024


class _Pushback:
    """A token iterator one token can be handed back to.

    A loop's values run until the next token that is not one, and that token
    belongs to whatever comes after the loop. One place to put it back is all
    the parser needs.
    """

    def __init__(self, tokens: Iterator[Tuple[str, str]]) -> None:
        self._tokens = tokens
        self._held: Optional[Tuple[str, str]] = None

    def next(self) -> Optional[Tuple[str, str]]:
        """The next token, or None at the end of the header."""
        if self._held is not None:
            token, self._held = self._held, None
            return token
        return next(self._tokens, None)

    def push(self, token: Tuple[str, str]) -> None:
        """Hand one token back, to be returned by the next call."""
        self._held = token


def _quoted(line: str, start: int) -> Tuple[str, int]:
    """One quoted value, and the offset just past it.

    The closing quote is the first one followed by whitespace or by the end of
    the line, which is what lets ``'Doe, J.'`` hold an apostrophe of its own.
    A quoted value cannot span lines, so a line that opens a quote and never
    closes it is read to its end rather than swallowing the line below.
    """
    quote = line[start]
    at = start + 1
    while True:
        end = line.find(quote, at)
        if end < 0:
            return line[start + 1 :], len(line)
        if end + 1 >= len(line) or line[end + 1].isspace():
            return line[start + 1 : end], end + 1
        at = end + 1


def _line_tokens(line: str) -> Iterator[Tuple[str, str]]:
    """The tokens of one line, up to a comment if it holds one.

    A ``#`` opens a comment only where a token could start, so one inside a
    word or inside a quoted value is an ordinary character.
    """
    at, end = 0, len(line)
    while at < end:
        if line[at].isspace():
            at += 1
            continue
        if line[at] == COMMENT:
            return
        if line[at] in QUOTES:
            value, at = _quoted(line, at)
            yield VALUE, value
            continue
        stop = at
        while stop < end and not line[stop].isspace():
            stop += 1
        token, at = line[at:stop], stop
        if token.startswith("_"):
            yield NAME, token
        elif token.lower() == LOOP_WORD:
            yield LOOP, token
        elif token.lower().startswith(DATA_PREFIX):
            yield DATA, token[len(DATA_PREFIX) :]
        else:
            yield VALUE, token


def _text_field(
    first: str, lines: Iterator[str], name: str, format_name: str
) -> Tuple[str, str]:
    """One ``;`` text field, and whatever followed it on its closing line.

    Returned rather than yielded as several values: a text field is one value
    however many lines it was written over. A field that never closes is
    refused, because everything behind it would otherwise be read as part of
    it, and a value swallowing the rest of a file is worse than no value.
    """
    parts = [first[1:]]
    for line in lines:
        if line.startswith(TEXT_FIELD):
            return "\n".join(parts), line[1:]
        parts.append(line)
    raise ValueError(
        f"Truncated {format_name} header in {name}: a text field opened and "
        "was never closed by a line beginning with a semicolon"
    )


def _tokenize(
    lines: Iterable[str], name: str, format_name: str
) -> Iterator[Tuple[str, str]]:
    """The CIF 1.1 subset the two dialects are written in, as tokens.

    What it covers: comments, single items, quoted values under either quote,
    multi-line text fields, and loops. What it does not, because no mmCIF or
    core CIF data file uses them: ``save_`` frames, which belong to dictionary
    files; the ``global_`` and ``stop_`` reserved words; and the CIF 2.0 list
    and table values. Any of those is read as an ordinary value, which is why a
    dictionary file is refused for its categories rather than described badly.

    A ``data_`` token is recognised wherever it appears rather than only at the
    start of a line, which a file quoting such a string as a value would be
    misread on. Files do not.
    """
    stream = iter(lines)
    while True:
        line = next(stream, None)
        if line is None:
            return
        if line.startswith(TEXT_FIELD):
            value, line = _text_field(line, stream, name, format_name)
            yield VALUE, value
        yield from _line_tokens(line)


def _read_loop(stream: _Pushback, columns: Dict[str, List[str]]) -> bool:
    """One ``loop_`` table. False once the coordinates have been reached.

    The names come first, one per line, and then the rows run together: a
    reader knows where a row ends only by counting the names. So the values are
    counted through whether or not this loop is one of the collected
    categories, which is what lets an uncollected loop be skipped without
    mistaking one of its values for the next item name.
    """
    names: List[str] = []
    while True:
        token = stream.next()
        if token is None:
            return True
        if token[0] == NAME:
            names.append(token[1].lower())
            continue
        stream.push(token)
        break

    if not names:
        return True
    if names[0].startswith(ATOM_SITE_PREFIXES):
        return False

    kept = [_collected(name) for name in names]
    at = 0
    while True:
        token = stream.next()
        if token is None or token[0] != VALUE:
            if token is not None:
                stream.push(token)
            return True
        column = at % len(names)
        if kept[column]:
            columns.setdefault(names[column], []).append(token[1])
        at += 1


def _category(name: str) -> str:
    """The PDBx category an item name belongs to, or ``""`` for a core name."""
    lowered = name.lower()
    return lowered.split(".", 1)[0] if "." in lowered else ""


def _collected(name: str) -> bool:
    """Whether this handler stores what an item of this name says."""
    return name.lower() in CORE_ITEMS or _category(name) in PDBX_CATEGORIES


def _parse(tokens: Iterator[Tuple[str, str]]) -> Tuple[Optional[str], Dict]:
    """The first data block's name, and the values of its collected items.

    One dict for both spellings an item has: a single item becomes a column of
    one value and a loop column becomes a column of as many as it has rows, so
    a caller asking what ``_exptl.method`` says gets the same answer whether
    the file wrote one method as an item or two as a table. It is also how a
    category's row count is recovered, in :func:`_rows`.

    The first block only. A file holding several is described by its first, and
    the rest are not read; reading them would mean reading past a coordinate
    table, which is the read this handler exists not to do.
    """
    stream = _Pushback(tokens)
    block: Optional[str] = None
    columns: Dict[str, List[str]] = {}
    while True:
        token = stream.next()
        if token is None:
            break
        kind, text = token
        if kind == DATA:
            if block is not None:
                break
            block = text
        elif block is None:
            continue  # Anything in front of the first block states nothing.
        elif kind == LOOP:
            if not _read_loop(stream, columns):
                break
        elif kind == NAME:
            value = stream.next()
            if value is None:
                break
            if value[0] != VALUE:
                # An item with no value: malformed, and the token that arrived
                # instead is the start of whatever comes next.
                stream.push(value)
                continue
            if _collected(text):
                columns.setdefault(text.lower(), []).append(value[1])
    return block, columns


def _stated(value: Optional[str]) -> Optional[str]:
    """What a file states, or None where it states nothing.

    Whitespace is normalised because a value may arrive from a text field,
    which is a paragraph the file wrapped for column width; the column it was
    wrapped at is not part of what was written.
    """
    if value is None:
        return None
    text = " ".join(value.split())
    return None if not text or text in NULL_VALUES else text


def _one(columns: Dict, name: str) -> Optional[str]:
    """The first value stated under ``name``, or None if none is.

    The first, not the last: a file stating one value twice states it once, and
    taking the first keeps the answer the same however many times it is
    written.
    """
    for value in columns.get(name, ()):
        stated = _stated(value)
        if stated is not None:
            return stated
    return None


def _all(columns: Dict, name: str) -> List[str]:
    """Every value stated under ``name``, in the order the file wrote them."""
    stated = (_stated(value) for value in columns.get(name, ()))
    return [value for value in stated if value is not None]


def _rows(columns: Dict, category: str) -> int:
    """How many rows a category holds, whichever way it was written.

    A category of one row is written as single items rather than as a loop of
    one, and both come back as the length of its longest column.
    """
    prefix = f"{category}."
    return max(
        (len(values) for name, values in columns.items() if name.startswith(prefix)),
        default=0,
    )


def _split_list(text: Optional[str]) -> List[str]:
    """A comma-separated list, stripped, with its empty entries dropped."""
    items = (text or "").split(VALUE_SEPARATOR)
    return [item.strip() for item in items if item.strip()]


def _distinct(values: Iterable[str]) -> List[str]:
    """The values, deduplicated, in the order they were first written."""
    seen: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def _strip_uncertainty(text: str) -> str:
    """``10.1234(4)`` as ``10.1234``: the number, without the value about it."""
    return UNCERTAINTY.sub("", text).strip()


def _number(text: Optional[str]) -> Optional[float]:
    """One measured number, or None when the text is not one.

    Finite only. ``inf`` and ``nan`` are spellings ``float`` accepts and no
    crystallographic quantity has, and a length reported as infinite would be a
    worse answer than no length.
    """
    if text is None:
        return None
    try:
        value = float(_strip_uncertainty(text))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _integer(text: Optional[str]) -> Optional[int]:
    """One counted number, or None when the text is not one."""
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _is_pdbx(columns: Dict) -> bool:
    """Whether the block is PDBx: it identifies an entry, names the dictionary
    it conforms to, or describes a structure."""
    return any(marker in columns for marker in PDBX_MARKERS) or any(
        _category(name) == "_struct" for name in columns
    )


def _is_small_molecule(columns: Dict) -> bool:
    """Whether the block is core CIF: it states a cell or a formula.

    Asked only once PDBx has been ruled out, because a PDBx entry states both
    of those too, under names of its own.
    """
    return any(marker in columns for marker in CORE_MARKERS)


def _counted(count: int, singular: str, several: str) -> str:
    """``1 polymer entity``, ``2 polymer entities``.

    Not :func:`~croissant_baker.handlers.utils.plural`, whose trailing ``s``
    does not spell the plural of every noun this description uses.
    """
    return f"{count} {singular}" if count == 1 else f"{count} {several}"


def _deposited(date: Optional[str]) -> str:
    """``deposited 1998-01-12``, or nothing when the file states no date."""
    return f"deposited {date}" if date else ""


def _determined(metadata: dict) -> str:
    """How the structure was determined, and at what resolution.

    The resolution is written to two decimals because that is the precision the
    item it came from is quoted at.
    """
    methods = ", ".join(metadata.get("experimental_methods", []))
    resolution = metadata.get("resolution_angstrom")
    if methods and resolution is not None:
        return f"{methods} at {resolution:.2f} A"
    if resolution is not None:
        return f"{resolution:.2f} A resolution"
    return methods


def _pdbx_metadata(name: str, columns: Dict) -> dict:
    """What a PDBx block states, and the sentence that states it.

    Every field is absent rather than empty when the item behind it is: a key
    holding nothing states nothing, and a stripped-down entry carries only a
    few of these.
    """
    metadata: dict = {"encoding_format": MMCIF_ENCODING_FORMAT}
    for key, value in (
        ("entry_id", _one(columns, "_entry.id")),
        ("title", _one(columns, "_struct.title")),
        (
            "deposition_date",
            _one(columns, "_pdbx_database_status.recvd_initial_deposition_date"),
        ),
        ("experimental_methods", _all(columns, "_exptl.method")),
        ("resolution_angstrom", _resolution(columns)),
        (
            "model_count",
            _integer(
                _one(columns, "_pdbx_nmr_ensemble.conformers_submitted_total_number")
            ),
        ),
        ("polymer_entity_count", _rows(columns, "_entity_poly")),
        ("chain_count", _chain_count(columns)),
        ("asym_unit_count", _asym_unit_count(columns)),
        ("classification", _one(columns, "_struct_keywords.pdbx_keywords")),
        ("keywords", _split_list(_one(columns, "_struct_keywords.text"))),
        ("dictionary", _dictionary(columns)),
    ):
        if value:
            metadata[key] = value
    metadata["description"] = _describe_pdbx(name, metadata)
    return metadata


def _resolution(columns: Dict) -> Optional[float]:
    """The resolution the refinement states, or the reconstruction's.

    A structure determined by microscopy has no refinement resolution, and one
    determined by NMR states neither; an entry writing the refinement item as
    ``?`` states that the value is unknown, and :func:`_stated` reports that as
    nothing rather than as a number.
    """
    return _number(_one(columns, "_refine.ls_d_res_high")) or _number(
        _one(columns, "_em_3d_reconstruction.resolution")
    )


def _chain_count(columns: Dict) -> int:
    """How many polymer chains the entry holds.

    From the strand identifiers the polymer entities name, one comma-separated
    list per entity, which is the count the PDB handler reports for the same
    entry from its ``COMPND`` ``CHAIN:`` tokens. An asym unit is not a chain: a
    deposit gives one to every copy of every ligand and one to its ordered
    solvent, so a four-chain haemoglobin carries nine, and that count is
    reported under :func:`_asym_unit_count` instead. It stands in here only for
    a block carrying no polymer entity at all, which then says nothing else
    about how many chains it holds.

    Chains are counted rather than listed: how many there are is structure, and
    which letters they were given is not.
    """
    if not _rows(columns, "_entity_poly"):
        return _rows(columns, "_struct_asym")
    strands = _all(columns, "_entity_poly.pdbx_strand_id")
    return len(_distinct(item for value in strands for item in _split_list(value)))


def _asym_unit_count(columns: Dict) -> int:
    """How many asym units the entry holds, one per ``_struct_asym`` row.

    Reported beside the chain count rather than as it, because the two answer
    different questions: how many chains a structure has, and how many
    instances of anything the coordinates are grouped into.
    """
    return _rows(columns, "_struct_asym")


def _dictionary(columns: Dict) -> str:
    """The dictionary the file declares it conforms to, and its version."""
    name = _one(columns, "_audit_conform.dict_name")
    version = _one(columns, "_audit_conform.dict_version")
    if not name:
        return ""
    return f"{name} {version}" if version else name


def _counts(metadata: dict) -> str:
    """The counts clause: entities, chains, asym units and models.

    The asym unit count is stated only where it differs from the chain count.
    An entry of one polymer and nothing else has one per chain, and stating the
    same number twice under two names reads as a distinction the file is not
    making.
    """
    stated = []
    for key, singular, several in (
        ("polymer_entity_count", "polymer entity", "polymer entities"),
        ("chain_count", "chain", "chains"),
        ("asym_unit_count", "asym unit", "asym units"),
        ("model_count", "model", "models"),
    ):
        if key not in metadata:
            continue
        if key == "asym_unit_count" and metadata[key] == metadata.get("chain_count"):
            continue
        stated.append(_counted(metadata[key], singular, several))
    return ", ".join(stated)


def _describe_pdbx(name: str, metadata: dict) -> str:
    """What the header says, in one sentence, with nothing else in it.

    Every clause is omitted when the items behind it are absent, so a block
    carrying only its entry id is described as an mmCIF structure and little
    more.
    """
    identity = ", ".join(
        part
        for part in (
            metadata.get("entry_id"),
            metadata.get("classification"),
            _deposited(metadata.get("deposition_date")),
        )
        if part
    )
    title = f"title: {metadata['title']}" if metadata.get("title") else ""
    clauses = [
        c for c in (identity, _determined(metadata), _counts(metadata), title) if c
    ]
    stated = f" ({'; '.join(clauses)})" if clauses else ""
    return (
        f"mmCIF structure {name}{stated}. Described from its header; no "
        "coordinate record was read."
    )


def _small_molecule_metadata(name: str, block: str, columns: Dict) -> dict:
    """What a core CIF block states, and the sentence that states it."""
    written = {key: _one(columns, item) for key, item in CELL_ITEMS}
    numbers = {key: _number(text) for key, text in written.items()}

    metadata: dict = {"encoding_format": CIF_ENCODING_FORMAT, "data_block": block}
    for key, value in (
        (
            "chemical_name",
            _one(columns, "_chemical_name_common")
            or _one(columns, "_chemical_name_systematic"),
        ),
        ("formula", _one(columns, "_chemical_formula_sum")),
        (
            "space_group",
            _one(columns, "_space_group_name_h-m_alt")
            or _one(columns, "_symmetry_space_group_name_h-m"),
        ),
        # All six or none: a cell is one description of one lattice, and three
        # edges without their angles do not describe it.
        ("cell", numbers if all(v is not None for v in numbers.values()) else None),
        ("wavelength", _number(_one(columns, "_diffrn_radiation_wavelength"))),
    ):
        if value:
            metadata[key] = value
    metadata["description"] = _describe_small_molecule(name, metadata, written)
    return metadata


def _describe_small_molecule(name: str, metadata: dict, written: Dict) -> str:
    """What the header says, in one sentence, with nothing else in it.

    The cell edges are written as the file wrote them, their uncertainties
    aside, rather than reformatted off the parsed floats: the digits a
    crystallographer sees here are then the digits in the file.
    """
    edges = ""
    if "cell" in metadata:
        edges = ", ".join(
            f"{key}={_strip_uncertainty(written[key])}" for key in CELL_EDGES
        )
    block = metadata.get("data_block")
    clauses = [
        clause
        for clause in (
            f"data block {block}" if block else "",
            metadata.get("chemical_name", ""),
            metadata.get("formula", ""),
            metadata.get("space_group", ""),
            edges,
        )
        if clause
    ]
    stated = f" ({'; '.join(clauses)})" if clauses else ""
    return (
        f"CIF crystal structure {name}{stated}. Described from its header; no "
        "atom record was read."
    )


def _opens_a_data_block(head: bytes) -> bool:
    """Whether the first thing this head states is a data block.

    Blank lines and comments are skipped, because a core CIF from the COD or
    the CSD opens with a banner of them. A head holding nothing else answers
    False: the file may state a block below, but not within the bytes read, and
    a claim made on bytes nobody looked at is a guess.
    """
    for raw in head.split(b"\n"):
        line = decode_line(raw).strip()
        if not line or line.startswith(COMMENT):
            continue
        return line.lower().startswith(DATA_PREFIX)
    return False


class CIFHandler(FileTypeHandler):
    """Handler for CIF structure files (``.cif``, ``.mmcif``).

    Reads the items and loops in front of the coordinate table and stops there.
    No coordinate row is ever parsed, and no RecordSet is emitted: the output is
    the FileObject the generator builds, carrying the description this handler
    wrote.
    """

    EXTENSIONS = (".cif", ".mmcif")
    FORMAT_NAME = "mmCIF"
    ENCODING_FORMAT = MMCIF_ENCODING_FORMAT
    FORMAT_DESCRIPTION = (
        "PDBx entry id, title, experimental method, resolution, entity and "
        "chain counts; small-molecule CIF formula and cell (header only)"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim a file named like a CIF that opens with a data block.

        Both conditions, because neither is sufficient on its own. The
        extension is not: ``.cif`` is also the Windows compiled-installation
        file, a setup-time binary that carries no structure and must not be
        described as one, and it is a generic enough three letters that other
        tools have taken it too. The data block is not either: a line opening
        ``data_`` is a shape any text file can wear, and the extension is what
        says this one is crystallographic. ``.mmcif`` is the second name,
        because that is what a tool writing both dialects calls the PDBx one.

        A file that cannot be read peeks as ``b""`` and is therefore not
        claimed; that is
        :meth:`~croissant_baker.sources.FileSource.peek`'s contract, and no
        handler repeats it.
        """
        if source.suffix not in self.EXTENSIONS:
            return False
        return _opens_a_data_block(source.peek(CLAIM_BYTES))

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Read one header, stopping at the coordinate table.

        Args:
            source: The file, already decompressed.
        """
        if not source.exists:
            raise FileNotFoundError(
                f"{self.FORMAT_NAME} file not found: {source.relative_path}"
            )

        name = str(source.relative_path)
        block, columns = self._read_header(source, name)
        if block is None:
            raise ValueError(
                f"Not a {self.FORMAT_NAME} file: {name} holds no data block, so "
                "there is nothing in it to describe"
            )

        metadata = {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
        }
        # The one thing this handler emits is the description these build.
        # Built here rather than in build_croissant, which runs after the
        # FileObject is staged, and from the logical name, which is the only
        # one extraction is given.
        if _is_pdbx(columns):
            metadata.update(_pdbx_metadata(source.name, columns))
        elif _is_small_molecule(columns):
            metadata.update(_small_molecule_metadata(source.name, block, columns))
        else:
            raise ValueError(
                f"Unsupported {self.FORMAT_NAME} dialect in {name}: the data "
                "block states neither a PDBx entry, dictionary or structure "
                "category nor a small-molecule cell or formula, so this handler "
                "cannot describe it"
            )
        return metadata

    def _read_header(
        self, source: FileSource, name: str
    ) -> Tuple[Optional[str], Dict[str, List[str]]]:
        """The first block's name and collected items, read once.

        Read through :class:`~croissant_baker.handlers.utils.PrefixLines`,
        which is bounded in bytes and delivers the tail of a file that ends
        without a line ending as the line it is. The caps are checked once a
        chunk, because a header that never reaches a coordinate table, and a
        line that never ends, are each owed a refusal before the file does.
        """
        try:
            with source.open() as stream:
                reader = PrefixLines(
                    stream,
                    MAX_HEADER_BYTES + 1,
                    on_chunk=lambda read, pending: self._still_a_header(
                        pending, read, name
                    ),
                )
                return _parse(_tokenize(reader, name, self.FORMAT_NAME))
        except UNREADABLE as exc:
            raise ValueError(
                f"Failed to read {self.FORMAT_NAME} file {name}: {exc}"
            ) from exc

    def _still_a_header(self, line_bytes: int, header_bytes: int, name: str) -> None:
        """Refuse a read that has gone past what a header can be, saying which.

        Two caps rather than one. A file with no coordinate table in it never
        reaches the stop, and a dictionary or a powder pattern is exactly that,
        so the header cap ends the read there. A file with no line ending in it
        does not reach the header cap either, not before holding and re-copying
        every byte on the way to it, so the line cap ends that one first.
        """
        if line_bytes > MAX_LINE_BYTES:
            raise ValueError(
                f"Not a {self.FORMAT_NAME} file: {name} runs to {line_bytes} "
                f"bytes with no line ending, past the {MAX_LINE_BYTES} a line of "
                "a CIF header can be"
            )
        if header_bytes > MAX_HEADER_BYTES:
            raise ValueError(
                f"Not a {self.FORMAT_NAME} file: the header of {name} exceeded "
                f"the {MAX_HEADER_BYTES}-byte cap without reaching a coordinate "
                "table"
            )

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """Nothing: a structure is described as a file, by its own description.

        Coordinate rows are records of a molecule, not of a dataset schema:
        they are one geometry of one entity rather than rows anyone would read
        a column out of, and a RecordSet naming fields no consumer can read
        through Croissant would be a promise nobody can keep. The same
        reasoning as PDB.
        """
        return BuildResult([], [])
