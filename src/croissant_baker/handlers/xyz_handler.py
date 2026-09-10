"""XYZ handler: the first frame's header, and none of the geometry behind it.

An XYZ file is an atom count, a comment line, and then one line of ``symbol x y
z`` per atom; a trajectory repeats that frame back to back until the simulation
ends. Every frame after the first restates the same two structural facts, and
the coordinates themselves are the data rather than anything said about it, so
the first frame's header is what is read and the megabytes behind it are not.

The comment line is the one place an XYZ says anything about itself, and it is
emitted as it stands: a title the writer typed, or nothing, or the extended-XYZ
``key=value`` string whose ``Properties=`` term names the columns the atom lines
carry. Those names are read off that term because the file declares them; what
the columns hold is never looked at.

No RecordSet: atoms are records of a structure, not of a dataset schema. What
this handler produces is a described FileObject, through the ``description`` key
the generator honours, exactly as the FASTA and FASTQ handlers do.
"""

import re
from typing import List, Optional

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.utils import plural, read_prefix_chunks
from croissant_baker.sources import UNREADABLE, FileSource

#: XYZ has no IANA registration. ``chemical/*`` is the family the chemistry
#: tools have used for these files since long before anyone registered
#: anything, so it is the type a reader recognises; the ``x-`` form marks it as
#: unregistered, as ``text/x-vcf`` and ``text/x-fasta`` already do in this tree.
ENCODING_FORMAT = "chemical/x-xyz"

#: The two lines a frame opens with: the atom count, then the comment.
HEADER_LINES = 2

#: The lines a claim or a description needs: the two header lines, and the atom
#: line under them that tells a real frame from a numbered list.
FRAME_LINES = HEADER_LINES + 1

#: The symbol an atom line opens with, in one column: an element symbol, or the
#: atomic number half the writers put there instead.
SYMBOL_TOKENS = 1

#: The coordinates that follow it, which are what makes the line an atom line.
COORDINATE_TOKENS = 3

#: An atom line is the symbol and those coordinates. Further columns are allowed
#: and common, so this is a minimum rather than a count.
ATOM_LINE_TOKENS = SYMBOL_TOKENS + COORDINATE_TOKENS

#: An atom count, and nothing else on the line. Spelled out rather than left to
#: ``str.isdigit``, which is true of superscripts and of digits in every script.
ATOM_COUNT = re.compile(r"[0-9]+")

#: The term that makes a comment line an extended-XYZ one. Its presence is the
#: whole test: the dialect is what the file declares, whether or not the value
#: behind the term turns out to be readable.
EXTENDED_MARKER = "Properties="

#: The value of that term, as ``Properties=species:S:1:pos:R:3``. Quoted in some
#: writers' output, so both spellings are matched; the value itself is a run of
#: ``name:type:count`` triples.
PROPERTIES = re.compile(EXTENDED_MARKER + r'(?:"([^"]*)"|(\S+))')

#: How many fields a ``Properties=`` triple holds, the name being the first.
PROPERTY_FIELDS = 3

#: Enough of the head to see the frame's shape. Three short lines fit many
#: times over; a peek that stops short of the third leaves the decision to the
#: extension, and ``extract`` reports what it then finds.
CLAIM_BYTES = 4096

#: The prefix the first frame's header must end inside. Bounded rather than
#: three lines taken off the stream, which on a file holding no line ending at
#: all reads the whole of it, and reading the whole of it is the one thing this
#: handler exists not to do. 64 KiB because the header is two lines and the
#: check of the first atom line needs one more: a comment line is a title or an
#: extended-XYZ term list, the longest of which is a lattice, a property list
#: and a handful of scalars, and an atom line is a symbol and a few numbers.
HEAD_BYTES = 64 * 1024


class XYZHandler(FileTypeHandler):
    """Handler for XYZ Cartesian coordinate files (``.xyz``).

    The first frame's header is read and the rest of the file is not, so a
    trajectory of a million frames costs the same read as a single structure.

    No RecordSet is emitted. The output is the FileObject the generator builds,
    carrying the description this handler wrote.
    """

    EXTENSIONS = (".xyz",)
    FORMAT_NAME = "XYZ"
    ENCODING_FORMAT = ENCODING_FORMAT
    FORMAT_DESCRIPTION = (
        "Atom count and comment line of the first frame; frames are not counted"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim a file named like an XYZ whose head is shaped like a frame.

        Both conditions, because neither survives on its own. A leading integer
        on a line of its own is also how a numbered list, a record count and a
        line-oriented log all open, which is too little to own a file on; the
        extension cannot decide alone either, because ``.xyz`` is a suffix a
        user may put on anything, and several unrelated formats have used it.
        Together they are the frame's own shape: a count, a comment line that
        may say anything at all, and under them a line of a symbol and three
        numbers.

        The third line is checked only when the head holds one. A file of two
        lines is a frame of no atoms, which is legal, and a peek that stops
        short of the third line leaves ``extract`` to report what it finds.

        A file that cannot be read peeks as ``b""`` and is therefore not
        claimed; that is
        :meth:`~croissant_baker.sources.FileSource.peek`'s contract, and no
        handler repeats it.
        """
        if source.suffix not in self.EXTENSIONS:
            return False
        lines = _decode_lines(source.peek(CLAIM_BYTES))
        if not lines or _atom_count_of(lines[0]) is None:
            return False
        if len(lines) < FRAME_LINES:
            return True
        return _is_atom_line(lines[HEADER_LINES])

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Read the first frame's header, and stop there.

        Args:
            source: The file, already decompressed.
        """
        if not source.exists:
            raise FileNotFoundError(
                f"{self.FORMAT_NAME} file not found: {source.relative_path}"
            )

        name = str(source.relative_path)
        lines = self._read_first_frame(source, name)
        atom_count = self._atom_count(lines, name)
        self._check_first_atom(lines, atom_count, name)

        comment = lines[1].strip() if len(lines) > 1 else ""
        properties = _properties(comment)
        extended = properties is not None

        meta = {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": self.ENCODING_FORMAT,
            "atom_count": atom_count,
            "comment": comment,
            "extended_xyz": extended,
            # The one thing this handler emits. Built here rather than in
            # build_croissant, which runs after the FileObject is staged, and
            # from the logical name, which is the only one extraction is given.
            "description": self._describe(source.name, atom_count, comment, properties),
        }
        if extended:
            meta["properties"] = properties
        return meta

    def _read_first_frame(self, source: FileSource, name: str) -> List[str]:
        """The first three lines, decoded and stripped of their endings.

        Taken from a bounded prefix rather than as three lines off the stream: a
        stream iterated by line hands back the whole file as one line when the
        file holds no line ending, so a frame whose header does not end inside
        the prefix is reported rather than read for.

        Permissively decoded: a comment line is whatever a writer put there, and
        a stray byte in it is not a reason to refuse a file whose frame is
        otherwise readable.
        """
        head = b""
        try:
            with source.open() as stream:
                for chunk in read_prefix_chunks(stream, HEAD_BYTES):
                    head += chunk
                    if head.count(b"\n") >= FRAME_LINES:
                        break
        except UNREADABLE as exc:
            raise ValueError(
                f"Failed to read {self.FORMAT_NAME} file {name}: {exc}"
            ) from exc

        if not head:
            raise ValueError(
                f"Empty {self.FORMAT_NAME} file: {name} holds no frame to describe"
            )
        return _decode_lines(head)[:FRAME_LINES]

    def _atom_count(self, lines: List[str], name: str) -> int:
        """The first line as a count, or a refusal saying what is there instead.

        A count that is not a count means the file is not the format it is named
        for, and every value below it would be read out of the wrong lines.
        """
        count = _atom_count_of(lines[0])
        if count is None:
            raise ValueError(
                f"Not an {self.FORMAT_NAME} file: the first line of {name} is "
                f"{lines[0].strip()!r}, which is not an atom count"
            )
        return count

    def _check_first_atom(self, lines: List[str], atom_count: int, name: str) -> None:
        """Refuse a frame that promises atoms and does not carry one.

        Only the first line is checked, because only the first line is read. A
        count of zero promises nothing, and a frame of no atoms is legal: a
        trajectory writer emits one for an empty cell.
        """
        if atom_count == 0:
            return
        if len(lines) < FRAME_LINES:
            raise ValueError(
                f"Truncated {self.FORMAT_NAME} frame in {name}: the header "
                f"declares {plural(atom_count, 'atom')} and no atom line "
                f"follows it within {HEAD_BYTES} bytes"
            )
        if not _is_atom_line(lines[HEADER_LINES]):
            raise ValueError(
                f"Malformed {self.FORMAT_NAME} frame in {name}: the first atom "
                f"line is {lines[HEADER_LINES].strip()!r}, which is not a "
                f"symbol followed by {COORDINATE_TOKENS} coordinates"
            )

    def _describe(
        self,
        file_name: str,
        atom_count: int,
        comment: str,
        properties: Optional[List[str]],
    ) -> str:
        """What the FileObject says about the file, from the frame just read.

        The clause after the count is whichever of the two the comment line
        turned out to be. An extended-XYZ comment is a term list, and quoting a
        lattice back at a reader says less than naming the columns the file
        declares; a blank comment gets no clause at all, because quoting nothing
        reads as an empty title rather than as no title.
        """
        detail = plural(atom_count, "atom")
        if properties:
            detail += f"; extended XYZ, properties: {', '.join(properties)}"
        elif properties is not None:
            detail += "; extended XYZ"
        elif comment:
            detail += f'; comment: "{comment}"'
        return (
            f"{self.FORMAT_NAME} coordinates {file_name} ({detail}). Described "
            "from its first frame; no further frame or coordinate was read."
        )

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """Nothing: an XYZ is described as a file, by the description it carries.

        Atoms are records of a structure, not of a dataset schema, and a
        RecordSet naming columns no consumer can read through Croissant would be
        a promise nobody can keep. The same reasoning as FASTA and FASTQ.
        """
        return BuildResult([], [])


def _decode_lines(head: bytes) -> List[str]:
    """``head`` as lines, permissively decoded and stripped of their endings.

    Split on ``\\n`` alone rather than by ``str.splitlines``, which also breaks
    on form feeds and on the Unicode line separators: a comment line is free
    text, and a byte inside it must not turn one line into two.
    """
    return [line.rstrip("\r") for line in head.decode("utf-8", "replace").split("\n")]


def _atom_count_of(line: str) -> Optional[int]:
    """The line as a non-negative atom count, or None if it is not one."""
    text = line.strip()
    return int(text) if ATOM_COUNT.fullmatch(text) else None


def _is_atom_line(line: str) -> bool:
    """Whether a line is a symbol followed by its three coordinates.

    The three columns after the symbol, which is where the format puts them,
    rather than the last three on the line. What a frame carries past ``x y z``
    is the writer's own and need not be numeric at all: an extended-XYZ file
    declaring ``tags:S:1`` or ``molecule:S:1`` ends every atom line in a word,
    and judging the line by its tail would refuse a dialect the format is
    routinely written in.

    The symbol itself is only required to be present, not to be an element:
    half the writers put an atomic number there instead. ``str.split`` on
    whitespace yields no empty token, so a line of at least four of them has a
    symbol in the first column by construction.
    """
    tokens = line.split()
    if len(tokens) < ATOM_LINE_TOKENS:
        return False
    try:
        for token in tokens[SYMBOL_TOKENS : SYMBOL_TOKENS + COORDINATE_TOKENS]:
            float(token)
    except ValueError:
        return False
    return True


def _properties(comment: str) -> Optional[List[str]]:
    """The column names an extended-XYZ comment declares, or None if it is not one.

    An empty list rather than None for a ``Properties=`` term that declares no
    readable name: the file still says it is extended XYZ, which is a fact about
    the format, and the names are what could not be read from it.
    """
    if EXTENDED_MARKER not in comment:
        return None
    match = PROPERTIES.search(comment)
    if match is None:
        return []
    value = match.group(1) or match.group(2) or ""
    fields = value.split(":")
    if len(fields) < PROPERTY_FIELDS:
        return []
    return [name for name in fields[::PROPERTY_FIELDS] if name]
