"""SMILES handler: the shape of the lines, and no molecule out of them.

A SMILES file is one molecule per line, written as a structure and then, more
often than not, whitespace and a name or a registry identifier beside it. What
the file never says is which of those it holds: there is no header the format
requires, no delimiter it fixes and no column count it declares. So the layout
is read off a bounded sample of the lines and reported as what the sample
showed, with the number of lines it was read from said alongside it, because a
layout inferred from a thousand lines is a claim about a thousand lines.

Nothing from a data line reaches the output. The structure is the data, and the
name beside it is a label a depositor chose for a compound; the column names
are emitted only when the file wrote a header line stating them, which is the
one line in the file that describes rather than carries a molecule.
"""

import string
from typing import List, Optional, Tuple

import mlcroissant as mlc

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.utils import (
    PrefixLines,
    allocate_record_set_ids,
    decode_line,
    display_name,
    make_field_id,
    plural,
)
from croissant_baker.sources import UNREADABLE, FileSource

#: SMILES has no IANA registration. ``chemical/x-*`` is the family
#: cheminformatics tools register their types under, and
#: ``chemical/x-daylight-smiles`` is the member of it the format is served as.
ENCODING_FORMAT = "chemical/x-daylight-smiles"

#: The character that opens a comment line in the dialects that have one. It
#: cannot open a structure: outside a bracket atom ``#`` is a triple bond, and
#: no SMILES begins with a bond.
COMMENT_PREFIX = "#"

#: The bound on the sample, in bytes and in lines, whichever ends first. A
#: thousand lines is far more than a layout needs and far less than a library
#: holds, and the byte bound is what stops a file whose lines are long or, in
#: the degenerate case, absent: a file holding no line ending at all is one
#: line, and reading it is reading the whole file.
SAMPLE_BYTES = 1024 * 1024
SAMPLE_LINES = 1000

#: Enough of the head to reach the first line that is not a comment. A file
#: whose comment preamble runs past this is claimed on its extension and
#: reported by :meth:`SMILESHandler.extract` for whatever the read then finds,
#: which is the FASTQ handler's arrangement for the same problem.
CLAIM_BYTES = 4096

#: The atoms OpenSMILES lets a structure write without brackets: the organic
#: subset, spelled aliphatic and aromatic. Two-letter symbols are matched
#: first, so ``Cl`` is chlorine rather than carbon followed by a stray ``l``.
ORGANIC_TWO_LETTER = ("Cl", "Br")
ORGANIC_ONE_LETTER = frozenset("BCNOPSFI")
AROMATIC_ONE_LETTER = frozenset("bcnops")

#: ``H`` is bracket-only in the specification, and writers emit it bare anyway;
#: a file a chemist can read is not one to refuse over that.
HYDROGEN = "H"

#: What may stand between atoms: branches, ring-bond numbers (a digit, or
#: ``%`` and two of them), the bonds ``-=#$:/\`` and the ``~`` of the pattern
#: dialects, ``.`` for a disconnection, ``*`` for a wildcard atom, ``@`` for
#: chirality outside a bracket, ``+`` and ``-`` for charge, ``>`` for the role
#: separator of a reaction SMILES, and ``{}`` for the stochastic objects
#: BigSMILES writes a polymer with.
STRUCTURE_CHARACTERS = frozenset(string.digits + "()=#-+@/\\.%*:$~>{}")

#: What may sit inside a bracket atom: an isotope, a symbol, its chirality,
#: its hydrogen count, its charge and its atom class.
BRACKET_CHARACTERS = frozenset(string.ascii_letters + string.digits + "+-@:*#")

#: The most columns one record set states. Every column is a field node in the
#: output, so a line carrying a hundred thousand of them costs minutes and
#: gigabytes to build and describes nothing anyone will read. A molecule table
#: is a structure, a name and a handful of properties; a line far past this is
#: a file of another format that happens to be named ``.smi``, and describing
#: it to here and saying so beats both truncating in silence and emitting a
#: record set the width of the line. The HDF5 handler caps its generic view the
#: same way, at the same number.
MAX_FIELDS = 300

#: The suffix the one record set per file is allocated under.
RECORD_SET_SUFFIX = "molecules"

#: What the columns are called when the file names none of them. The first is
#: the structure, which the format fixes; the second is a name or an identifier
#: often enough that calling it anything else would be less informative; past
#: those the position is the only thing there is to name a column by.
SMILES_COLUMN = "smiles"
NAME_COLUMN = "name"


def positional_column(index: int) -> str:
    """What to call the column at ``index`` when the file names nothing."""
    if index == 0:
        return SMILES_COLUMN
    if index == 1:
        return NAME_COLUMN
    return f"column_{index + 1}"


def first_field(line: str) -> str:
    """The line up to its first space or tab, which is where the structure sits.

    Not ``str.split()``, which drops a leading run of whitespace and so reads
    the second field of an indented line as its first. A line that opens with
    whitespace has an empty first field, and an empty field is no structure.
    """
    return line.replace("\t", " ").split(" ", 1)[0]


def is_plausible_smiles(token: str) -> bool:
    """Whether ``token`` could be a SMILES string, read as symbols.

    The alphabet alone does not decide it. A SMILES is written with letters,
    digits and punctuation, and so is ``ethanol``, so a check that only asked
    which characters appeared would call every word in a header line and every
    compound name a structure. What separates them is that each letter run
    outside a bracket has to spell an atom of the organic subset, which
    ``ethanol`` and ``SMILES`` do not.

    Not a parser: valence, ring closures and bracket contents are not checked,
    because a file this handler cannot fully parse is still one whose column
    layout it can report, and refusing a structure over a rule this handler
    reads no chemistry from would be the guess it exists not to make.
    """
    index = 0
    saw_atom = False
    while index < len(token):
        char = token[index]
        if char == "[":
            closed = token.find("]", index)
            body = token[index + 1 : closed]
            if closed < 0 or not body or not set(body) <= BRACKET_CHARACTERS:
                return False
            saw_atom = True
            index = closed + 1
        elif token[index : index + 2] in ORGANIC_TWO_LETTER:
            saw_atom = True
            index += 2
        elif char in ORGANIC_ONE_LETTER or char in AROMATIC_ONE_LETTER:
            saw_atom = True
            index += 1
        elif char == HYDROGEN:
            index += 1
        elif char in STRUCTURE_CHARACTERS:
            index += 1
        else:
            return False
    return saw_atom


def is_data_line(line: str) -> bool:
    """Whether the line carries a record rather than a comment or a blank."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith(COMMENT_PREFIX)


def data_lines(lines: List[str]) -> List[str]:
    """The lines that carry a record, comments and blanks dropped."""
    return [line for line in lines if is_data_line(line)]


def header_before_the_molecules(records: List[str]) -> Optional[bool]:
    """Whether the records open with a header line, or None if they open with
    neither a header nor a molecule.

    The format has no way to declare a header, so one is detected: a first
    record whose first field is a structure is a molecule line, and a first
    record whose first field is not, followed by one whose first field is, is a
    file that wrote its column names down. ``SMILES Name`` is what RDKit's
    writer puts there, and it spells no structure.

    None is neither of those, which is a file this handler cannot read as a
    table of molecules. The claim and the layout both turn on this one
    question, and they answer it the same way or they disagree about the same
    file.
    """
    if not records:
        return None
    if is_plausible_smiles(first_field(records[0])):
        return False
    if len(records) > 1 and is_plausible_smiles(first_field(records[1])):
        return True
    return None


#: What the sampled lines separate their fields with. A tab anywhere in the
#: sample settles it, because a name field legitimately holds spaces and a
#: SMILES string holds neither.
TAB = "tab"
WHITESPACE = "whitespace"


def sampled_delimiter(lines: List[str]) -> str:
    """Which of the two the sample uses."""
    return TAB if any("\t" in line for line in lines) else WHITESPACE


def split_fields(line: str, delimiter: str) -> List[str]:
    """One line's fields, with the trailing empty ones a trailing delimiter
    invents dropped: a line ending in a tab carries no extra column."""
    fields = line.split("\t") if delimiter == TAB else line.split()
    while fields and not fields[-1]:
        fields.pop()
    return fields


class SMILESHandler(FileTypeHandler):
    """Handler for SMILES chemical structure files (``.smi``, ``.smiles``).

    One RecordSet per file, whose fields are the columns a bounded sample of
    the lines showed. No structure and no compound name is emitted; the column
    names come from the file's own header line when it wrote one, and from the
    column's position when it did not.
    """

    EXTENSIONS = (".smi", ".smiles")
    FORMAT_NAME = "SMILES"
    ENCODING_FORMAT = ENCODING_FORMAT
    FORMAT_DESCRIPTION = (
        "Column count and delimiter from a bounded sample of lines; "
        "one record per molecule"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim a declared SMILES extension whose head is molecule lines.

        Both conditions, because neither would do alone. The extension would
        claim any text a user happened to name ``.smi``, since the format has
        no magic bytes and no header line it requires. The lines would not
        either: read as symbols a structure is recognisable, but a single short
        line of it is also a plausible line of many other things, and the name
        is what says this file is a library of molecules.

        The head is read by the rule the layout is, so a file the claim takes
        is one ``extract`` can describe: a first record that is a structure, or
        a first record that is not with molecules under it, which is the header
        line half the writers and every large drop emit.

        A head that reaches no line other than comments is claimed, because the
        preamble may simply run past the peek; ``extract`` then reports what
        the read found.

        A file that cannot be read peeks as ``b""`` and is therefore not
        claimed; that is
        :meth:`~croissant_baker.sources.FileSource.peek`'s contract, and no
        handler repeats it.
        """
        if source.suffix not in self.EXTENSIONS:
            return False
        head = source.peek(CLAIM_BYTES)
        if not head:
            return False
        lines = [decode_line(raw) for raw in head.split(b"\n")]
        if len(head) == CLAIM_BYTES:
            # The peek stopped mid-line, and half a structure is not one.
            lines.pop()
        records = data_lines(lines)
        if not records:
            return True
        return header_before_the_molecules(records) is not None

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Read the head of a SMILES file and report the layout it shows.

        ``sampled_lines`` counts every line the read reached, comment and blank
        lines among them: it is the size of the sample the layout was read off,
        not a count of the molecules in the file, which would cost the whole
        file to know.

        Args:
            source: The file, already decompressed.
        """
        if not source.exists:
            raise FileNotFoundError(
                f"{self.FORMAT_NAME} file not found: {source.relative_path}"
            )

        name = str(source.relative_path)
        lines, exhausted = self._read_sample(source, name)
        delimiter = sampled_delimiter(lines)
        rows, has_header = self._layout(lines, delimiter, name)
        count = max(len(row) for row in rows)

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": self.ENCODING_FORMAT,
            "delimiter": delimiter,
            "has_header": has_header,
            "columns": self._columns(
                rows[0] if has_header else [], min(count, MAX_FIELDS)
            ),
            "column_count": count,
            "sampled_lines": len(lines),
            "sample_exhausted": exhausted,
        }

    def _read_sample(self, source: FileSource, name: str) -> Tuple[List[str], bool]:
        """The head of the file as lines, and whether the file ended inside it.

        Read through :class:`~croissant_baker.handlers.utils.PrefixLines`,
        which is bounded in bytes; the line bound stops the read here.

        A file whose bytes run out exactly at the byte bound is reported as
        exhausted: the reader looks one byte past the bound to tell the end of
        a stream from the bound that stopped it, so the sample really is the
        whole file.
        """
        lines: List[str] = []
        try:
            with source.open() as stream:
                reader = PrefixLines(stream, SAMPLE_BYTES)
                for line in reader:
                    lines.append(line)
                    if len(lines) == SAMPLE_LINES:
                        return lines, False
        except UNREADABLE as exc:
            raise ValueError(
                f"Failed to read {self.FORMAT_NAME} file {name}: {exc}"
            ) from exc
        return lines, not reader.bounded

    def _layout(
        self, lines: List[str], delimiter: str, name: str
    ) -> Tuple[List[List[str]], bool]:
        """The sampled records as fields, and whether the first of them names
        the columns rather than carrying a molecule.

        A header is detected rather than declared, by the rule ``claims`` reads
        the head with. A first record that is no structure and no header is a
        file this handler cannot describe, and it is reported rather than
        described as the molecule table it is not.
        """
        records = data_lines(lines)
        if not records:
            raise ValueError(
                f"Empty {self.FORMAT_NAME} file: {name} holds no molecule line "
                f"in its first {plural(len(lines), 'line')}"
            )
        rows = [split_fields(line, delimiter) for line in records]
        has_header = header_before_the_molecules(records)
        if has_header is None:
            raise ValueError(
                f"Not a {self.FORMAT_NAME} file: the first record of {name} "
                f"opens with '{first_field(records[0])}', which spells no "
                "structure, and the record after it spells none either, so the "
                "first column is not SMILES"
            )
        return rows, has_header

    def _columns(self, header: List[str], count: int) -> List[str]:
        """One name per column: the header's where it has one, the position's
        otherwise. A header naming fewer columns than the widest line carries
        leaves the rest to their position, rather than leaving them unnamed."""
        return [
            header[i] if i < len(header) and header[i] else positional_column(i)
            for i in range(count)
        ]

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """One record set per file: its columns, as the sample showed them."""
        allocated = allocate_record_set_ids(file_metas, [RECORD_SET_SUFFIX])
        record_sets = [
            self._record_set(meta, file_id, ids[RECORD_SET_SUFFIX])
            for meta, file_id, ids in zip(file_metas, file_ids, allocated)
        ]
        return BuildResult([], record_sets)

    def _record_set(self, meta: dict, file_id: str, rs_id: str) -> mlc.RecordSet:
        used: set = set()
        fields = [
            self._field(rs_id, file_id, used, index, name, meta["has_header"])
            for index, name in enumerate(meta["columns"])
        ]
        return mlc.RecordSet(
            id=rs_id,
            name=rs_id,
            description=_description(meta),
            fields=fields,
        )

    def _field(
        self,
        parent_id: str,
        file_id: str,
        used: set,
        index: int,
        name: str,
        has_header: bool,
    ) -> mlc.Field:
        """One column, sourced from the file and nothing narrower.

        Text, every one of them: a SMILES string is text, and so is whatever a
        writer put beside it, which this handler never reads far enough into to
        type any other way.

        No ``extract``: Croissant 1.1's extract grammar addresses columns of a
        format mlcroissant's reader knows how to open, and SMILES is not on
        that list, so a column reference here would be a promise nobody can
        keep. The same reasoning as VCF.
        """
        return mlc.Field(
            id=make_field_id(parent_id, name, used),
            name=name,
            description=_field_description(index, has_header),
            data_types=["sc:Text"],
            source=mlc.Source(file_object=file_id),
        )


def _columns_stated(meta: dict) -> str:
    """How many columns the record set names, and how many the file carried.

    The two differ only where :data:`MAX_FIELDS` stopped the list, and then the
    difference is the whole point: a reader is owed the columns that are not
    described here.
    """
    described = len(meta["columns"])
    total = meta.get("column_count", described)
    if total > described:
        return f"the first {described} of {plural(total, 'column')}"
    return plural(described, "column")


def _field_description(index: int, has_header: bool) -> str:
    """What one column is, said from where its name came."""
    if index == 0:
        return "SMILES string of the molecule, the first field of every line"
    named = (
        "named by the file's header line"
        if has_header
        else "which the file does not name"
    )
    return (
        f"Column {index + 1}, {named}. A line carrying fewer fields has left "
        "this one off."
    )


def _description(meta: dict) -> str:
    """The layout, in one deterministic sentence, with the sample it came from.

    The sample is stated rather than implied: a column count read from the
    first thousand lines of a library is a claim about those lines, and a
    consumer deciding whether to trust it needs to know how many there were.
    """
    stated = [f"{meta['delimiter']}-separated", _columns_stated(meta)]
    if meta["has_header"]:
        stated.append("header line")
    scope = "all" if meta["sample_exhausted"] else "the first"
    stated.append(f"from {scope} {plural(meta['sampled_lines'], 'line')}")
    return (
        f"Molecule records in {display_name(meta)} ({', '.join(stated)}). "
        "One molecule per line."
    )
