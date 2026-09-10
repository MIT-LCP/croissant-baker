"""PDB handler: the title section of a structure file, and no coordinate record.

A wwPDB structure file is fixed-column text: eighty columns per record, each
named by columns 1 to 6, with the title section written before the coordinates.
Everything that tells one deposit from another is in that title section, and
every part of it is a fact the file states about itself: which entry it is, what
it was classified as, when it was deposited, how the structure was determined
and at what resolution, and how many chains and models it holds. So the title
section is read and the coordinates behind it are not. The read stops at the
first ``MODEL``, ``ATOM`` or ``HETATM`` record, which means a structure of a
hundred thousand atoms costs the same read as a fragment of three.

Nothing in front of the title section says how long it is, so, as in SAM, what
bounds the read is that stop. A file that never reaches a coordinate record must
still stop somewhere, so the section is taken a chunk at a time and both a single
line and the section as a whole are capped.

Depositors are not emitted. The ``AUTHOR`` record names people and is
bibliographic rather than structural, and the dataset's own creator is a
command-line input rather than something read out of a file.

No RecordSet: a structure is a file, and atom records are not records of a
dataset schema. What this handler produces is a described FileObject, through
the ``description`` key the generator honours.
"""

from typing import List, Optional, Tuple

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.utils import MAX_HEADER_BYTES, plural, read_prefix_chunks
from croissant_baker.sources import UNREADABLE, FileSource

#: PDB has no IANA registration. ``chemical/x-pdb`` is the spelling the
#: chemical MIME family gave it, and the one RCSB and every molecular viewer
#: use, so it is preferred here over an invented ``application/x-`` form.
ENCODING_FORMAT = "chemical/x-pdb"

#: The records of the title section, in the order the format writes them.
TITLE_RECORDS = (
    "HEADER",
    "OBSLTE",
    "TITLE",
    "SPLIT",
    "CAVEAT",
    "COMPND",
    "SOURCE",
    "KEYWDS",
    "EXPDTA",
    "NUMMDL",
    "MDLTYP",
    "AUTHOR",
    "REVDAT",
    "SPRSDE",
    "JRNL",
    "REMARK",
)

#: The records between the title section and the coordinates: the sequence, the
#: features annotated on it, and the crystal frame the coordinates sit in.
STRUCTURE_RECORDS = (
    "DBREF",
    "DBREF1",
    "DBREF2",
    "SEQADV",
    "SEQRES",
    "MODRES",
    "HET",
    "HETNAM",
    "HETSYN",
    "FORMUL",
    "HELIX",
    "SHEET",
    "SSBOND",
    "LINK",
    "CISPEP",
    "SITE",
    "CRYST1",
    "ORIGX1",
    "ORIGX2",
    "ORIGX3",
    "SCALE1",
    "SCALE2",
    "SCALE3",
    "MTRIX1",
    "MTRIX2",
    "MTRIX3",
)

#: The coordinate section, and the bookkeeping records that close a file.
COORDINATE_RECORDS = (
    "MODEL",
    "ATOM",
    "HETATM",
    "ANISOU",
    "TER",
    "ENDMDL",
    "CONECT",
    "MASTER",
    "END",
)

#: Every record name PDB v3.3 defines. A file whose first line is named by one
#: of them is a PDB file; one whose first line is not, is not.
KNOWN_RECORDS = frozenset(TITLE_RECORDS + STRUCTURE_RECORDS + COORDINATE_RECORDS)

#: Where the read stops. Not every coordinate-section record: ``TER``, ``END``
#: and their neighbours only ever follow atoms, so a file reaching one has
#: already been stopped, and stopping on them as well would refuse to read the
#: ``END`` of a title section that carries no coordinates at all.
STOP_RECORDS = frozenset({"MODEL", "ATOM", "HETATM"})

#: Columns 1 to 6 name the record. Zero-based, so a slice.
RECORD_NAME_COLUMNS = 6

#: Column 11, where the text of a continued record begins, whichever of columns
#: 8 to 10 that record spends on its continuation number.
TEXT_COLUMN = 10

#: The columns the HEADER record fixes its three fields to, and the column the
#: SEQRES record fixes its chain identifier to.
CLASSIFICATION_COLUMNS = slice(10, 50)
DEPOSITION_DATE_COLUMNS = slice(50, 59)
ID_CODE_COLUMNS = slice(62, 66)
SEQRES_CHAIN_COLUMN = slice(11, 12)

#: What NUMMDL states its model count in, and REMARK its remark number.
MODEL_COUNT_COLUMNS = slice(10, 14)
REMARK_NUMBER_COLUMNS = slice(7, 10)

#: The remark that carries the resolution, and the word it introduces it with.
#: A structure determined without diffraction writes ``NOT APPLICABLE`` here,
#: which is a statement that there is no resolution rather than a number.
RESOLUTION_REMARK = "2"
RESOLUTION_MARKER = "RESOLUTION."

#: The token COMPND spends its chain identifiers on, and the separators of the
#: specification list it is written in.
CHAIN_TOKEN = "CHAIN"
SPECIFICATION_SEPARATOR = ";"
VALUE_SEPARATOR = ","

#: The largest single line this handler will accumulate. A PDB record is eighty
#: columns and then a line ending, so four kilobytes is fifty records' worth: a
#: first line longer than that is not a record, and reading on for the end of it
#: is reading the coordinates. The section as a whole is held to the shared
#: :data:`~croissant_baker.handlers.utils.MAX_HEADER_BYTES`, which is what a
#: file that never reaches a coordinate record is stopped by.
MAX_LINE_BYTES = 4096

#: Enough of the head to read the first record's name.
CLAIM_BYTES = RECORD_NAME_COLUMNS


def _decode(line: bytes) -> str:
    """One record as text, with the carriage return of a CRLF file gone.

    Decoded permissively: a PDB file is printable ASCII by specification, and a
    stray byte in a REMARK is not a reason to refuse a file whose structure is
    otherwise readable.
    """
    return line.decode("utf-8", "replace").rstrip("\r")


def _record_name(line: str) -> str:
    """Columns 1 to 6 of a record, which are what names it."""
    return line[:RECORD_NAME_COLUMNS].strip()


def _lines_of(lines: List[str], record: str) -> List[str]:
    """Every line of one record type, in the order the file wrote them."""
    return [line for line in lines if _record_name(line) == record]


def _text_of(lines: List[str], record: str) -> str:
    """The text columns of every ``record`` line, joined by single spaces.

    A record too long for one line is continued on the next, and columns 8 to 10
    say which continuation it is. The text either side of the break is written
    to be read as one run of words, so joining on single spaces is what puts it
    back together; the column padding a fixed-column format leaves behind is not
    part of what was written.
    """
    parts = [" ".join(line[TEXT_COLUMN:].split()) for line in _lines_of(lines, record)]
    return " ".join(part for part in parts if part)


def _split_list(text: str, separator: str) -> List[str]:
    """A separated list, stripped, with the empty entries a trailing separator
    leaves behind dropped."""
    return [item.strip() for item in text.split(separator) if item.strip()]


def _resolution(lines: List[str]) -> Optional[float]:
    """The resolution the second remark states, if it states one as a number.

    The first such remark, not the last: a file repeating it is stating one
    resolution twice, and taking the first keeps the answer the same however
    many times it is written.
    """
    for line in _lines_of(lines, "REMARK"):
        if line[REMARK_NUMBER_COLUMNS].strip() != RESOLUTION_REMARK:
            continue
        _, marker, tail = line.partition(RESOLUTION_MARKER)
        if not marker:
            continue
        words = tail.split()
        try:
            return float(words[0])
        except (IndexError, ValueError):
            continue
    return None


def _distinct(values: List[str]) -> List[str]:
    """The values, deduplicated, in the order they were first written."""
    seen: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def _compnd_chains(text: str) -> List[str]:
    """The chain identifiers the COMPND specification list names.

    COMPND is a list of ``TOKEN: value`` pairs separated by semicolons, one of
    which is ``CHAIN``, and a molecule present more than once in the structure
    names every copy there. Chains are counted rather than listed: how many
    there are is structure, and which letters they were given is not.
    """
    chains: List[str] = []
    for specification in text.split(SPECIFICATION_SEPARATOR):
        token, separator, value = specification.partition(":")
        if separator and token.strip() == CHAIN_TOKEN:
            chains += _split_list(value, VALUE_SEPARATOR)
    return _distinct(chains)


def _seqres_chains(lines: List[str]) -> List[str]:
    """The chain identifiers the sequence records carry, one per SEQRES line.

    The fallback for a file whose COMPND names no chain, which is what a
    stripped-down or tool-written entry usually looks like.
    """
    return _distinct(
        [line[SEQRES_CHAIN_COLUMN].strip() for line in _lines_of(lines, "SEQRES")]
    )


def _model_count(lines: List[str]) -> Optional[int]:
    """The model count NUMMDL states, when it states one that is a number."""
    for line in _lines_of(lines, "NUMMDL"):
        count = line[MODEL_COUNT_COLUMNS].strip()
        if count.isdigit():
            return int(count)
    return None


def _describe(name: str, metadata: dict) -> str:
    """What the title section says, in one sentence, with nothing else in it.

    Every clause is omitted when the record behind it is absent, so a fragment
    carrying no title section at all is described as a PDB file and nothing
    more. The resolution is written to two decimals because that is what the
    remark it came from writes.
    """
    identity = ", ".join(
        part
        for part in (
            metadata.get("id_code"),
            metadata.get("classification"),
            _deposited(metadata.get("deposition_date")),
        )
        if part
    )
    clauses = [clause for clause in (identity, _determined(metadata)) if clause]
    for key, noun in (("chain_count", "chain"), ("model_count", "model")):
        if key in metadata:
            clauses.append(plural(metadata[key], noun))
    if metadata.get("title"):
        clauses.append(f"title: {metadata['title']}")

    stated = f" ({'; '.join(clauses)})" if clauses else ""
    read = (
        "its header"
        if metadata.get("id_code")
        else "its header, which carries no ID code"
    )
    return (
        f"PDB structure {name}{stated}. Described from {read}; no coordinate "
        "record was read."
    )


def _deposited(date: Optional[str]) -> str:
    """``deposited 12-JAN-98``, or nothing when the file states no date."""
    return f"deposited {date}" if date else ""


def _determined(metadata: dict) -> str:
    """How the structure was determined, and at what resolution."""
    methods = ", ".join(metadata.get("experimental_methods", []))
    resolution = metadata.get("resolution_angstrom")
    if methods and resolution is not None:
        return f"{methods} at {resolution:.2f} A"
    if resolution is not None:
        return f"{resolution:.2f} A resolution"
    return methods


class PDBHandler(FileTypeHandler):
    """Handler for wwPDB structure files (``.pdb``, ``.ent``).

    Reads the records up to the first coordinate record and stops there. No
    atom is ever parsed, and no RecordSet is emitted: the output is the
    FileObject the generator builds, carrying the description this handler
    wrote.
    """

    EXTENSIONS = (".pdb", ".ent")
    FORMAT_NAME = "PDB"
    ENCODING_FORMAT = ENCODING_FORMAT
    FORMAT_DESCRIPTION = (
        "ID code, classification, title, experimental method, resolution, "
        "chain count (header only)"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim a file named like a PDB whose first line names a PDB record.

        Both conditions, because neither is sufficient on its own. The extension
        is not: ``.pdb`` is also the Microsoft program database, a binary of
        debugging symbols that carries no structure and must not be described as
        one. The first line is not either: six columns of upper-case letters are
        a shape any text file can wear, and the extension is what says this one
        is a structure. ``.ent`` is the second name, because that is what the
        RCSB archive calls its own copies of an entry.

        A file that cannot be read peeks as ``b""`` and is therefore not
        claimed; that is
        :meth:`~croissant_baker.sources.FileSource.peek`'s contract, and no
        handler repeats it.
        """
        if source.suffix not in self.EXTENSIONS:
            return False
        head = source.peek(CLAIM_BYTES)
        return _record_name(_decode(head.split(b"\n", 1)[0])) in KNOWN_RECORDS

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Read one title section, stopping at the first coordinate record.

        Args:
            source: The file, already decompressed.
        """
        if not source.exists:
            raise FileNotFoundError(
                f"{self.FORMAT_NAME} file not found: {source.relative_path}"
            )

        name = str(source.relative_path)
        lines, first = self._read_title_section(source, name)
        self._check_first_record(first, name)

        metadata = {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": self.ENCODING_FORMAT,
        }
        metadata.update(self._header_fields(lines))
        for key, value in (
            ("title", _text_of(lines, "TITLE")),
            (
                "experimental_methods",
                _split_list(_text_of(lines, "EXPDTA"), SPECIFICATION_SEPARATOR),
            ),
            ("keywords", _split_list(_text_of(lines, "KEYWDS"), VALUE_SEPARATOR)),
            ("resolution_angstrom", _resolution(lines)),
            ("model_count", _model_count(lines)),
        ):
            # Absent rather than empty: a key holding nothing states nothing,
            # and every one of these records is optional.
            if value:
                metadata[key] = value

        chains = _compnd_chains(_text_of(lines, "COMPND")) or _seqres_chains(lines)
        if chains:
            metadata["chain_count"] = len(chains)

        # The one thing this handler emits. Built here rather than in
        # build_croissant, which runs after the FileObject is staged, and from
        # the logical name, which is the only one extraction is given.
        metadata["description"] = _describe(source.name, metadata)
        return metadata

    def _header_fields(self, lines: List[str]) -> dict:
        """What the HEADER record states, each field from its own columns.

        A file written by a modelling tool often has no HEADER record at all,
        and one that does may still leave a field blank; either way what is not
        written is not reported.
        """
        headers = _lines_of(lines, "HEADER")
        if not headers:
            return {}
        header = headers[0]
        fields = {
            "id_code": header[ID_CODE_COLUMNS].strip(),
            "classification": header[CLASSIFICATION_COLUMNS].strip(),
            "deposition_date": header[DEPOSITION_DATE_COLUMNS].strip(),
        }
        return {key: value for key, value in fields.items() if value}

    def _check_first_record(self, first: Optional[str], name: str) -> None:
        """Refuse a file whose first line names no PDB record, saying which.

        The first line only: a fragment written by a modelling tool opens at
        ``ATOM`` and carries no title section, and it is a PDB file with nothing
        in its header rather than a file this handler cannot read.
        """
        if first is None:
            raise ValueError(
                f"Empty {self.FORMAT_NAME} file: {name} holds no record to describe"
            )
        if first not in KNOWN_RECORDS:
            raise ValueError(
                f"Not a {self.FORMAT_NAME} file: the first line of {name} is "
                f"named {first!r}, which is not a PDB record name"
            )

    def _read_title_section(
        self, source: FileSource, name: str
    ) -> Tuple[List[str], Optional[str]]:
        """Every record up to the first coordinate record, and the first name.

        Taken a chunk at a time rather than a line at a time, for the reason the
        SAM handler is: a stream iterated by line hands back the whole file as
        one line when the file holds no line ending, and reading the whole file
        is the one thing this handler exists not to do.

        The first record's name is returned whether or not the record was kept,
        because it is what says the file is a PDB at all, and a fragment opening
        at ``ATOM`` keeps nothing.
        """
        lines: List[str] = []
        first: Optional[str] = None
        pending = b""
        read = 0
        try:
            with source.open() as stream:
                for chunk in read_prefix_chunks(stream, MAX_HEADER_BYTES + 1):
                    read += len(chunk)
                    complete = (pending + chunk).split(b"\n")
                    # The tail after the last line ending is not yet a line.
                    pending = complete.pop()
                    for raw in complete:
                        line = _decode(raw)
                        record = _record_name(line)
                        first = record if first is None else first
                        if record in STOP_RECORDS:
                            return lines, first
                        lines.append(line)
                    self._still_a_title_section(len(pending), read, name)
                # End of file inside the title section: what is left of it is
                # the last record, written without a line ending.
                if pending:
                    line = _decode(pending)
                    record = _record_name(line)
                    first = record if first is None else first
                    if record not in STOP_RECORDS:
                        lines.append(line)
        except UNREADABLE as exc:
            raise ValueError(
                f"Failed to read {self.FORMAT_NAME} file {name}: {exc}"
            ) from exc
        return lines, first

    def _still_a_title_section(
        self, line_bytes: int, section_bytes: int, name: str
    ) -> None:
        """Refuse a read that has gone past what a title section can be.

        Two caps rather than one: a structure annotated with every remark it is
        entitled to runs to hundreds of kilobytes of REMARK records, and a
        single line of that size is not a record.
        """
        if line_bytes > MAX_LINE_BYTES:
            raise ValueError(
                f"Not a {self.FORMAT_NAME} file: {name} runs to {line_bytes} "
                f"bytes with no line ending, past the {MAX_LINE_BYTES} a record "
                "can be"
            )
        if section_bytes > MAX_HEADER_BYTES:
            raise ValueError(
                f"Not a {self.FORMAT_NAME} file: the header of {name} runs past "
                f"{MAX_HEADER_BYTES} bytes without reaching a coordinate record"
            )

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """Nothing: a structure is described as a file, by its own description.

        Atom records are records of a molecule, not of a dataset schema: they
        are one geometry of one entity rather than rows anyone would read a
        column out of, and a RecordSet naming fields no consumer can read
        through Croissant would be a promise nobody can keep. The same reasoning
        as BAM and FASTA.
        """
        return BuildResult([], [])
