"""The header an MDL molfile opens with, in either of its two layouts.

A molfile is four fixed lines and then a connection table. The first line is the
molecule's title, the second names the program that wrote it, the third is a
free comment, and the fourth is the counts line. That fourth line is the only
one that says which layout the rest of the file is written in, and the layouts
disagree about where the counts themselves live:

- **V2000** puts them on the counts line, in fixed-width fields:
  ``aaabbblllfffcccsssxxxrrrpppiiimmmvvvvvv``, atoms in columns 1-3, bonds in
  columns 4-6, and the version literal in columns 34-39.
- **V3000** puts zeros there and writes the real counts further down, on the
  ``M  V30 COUNTS na nb nsg n3d chiral`` line inside ``M  V30 BEGIN CTAB``.

This module reads exactly that far and no further: the atom block, the bond
block and the properties block are the molecule, not metadata about it, and the
handlers that call this exist not to read them.

A counts line carrying neither version literal is refused rather than guessed
at. Such a line is often four or five integers wide and would parse as
fixed-width counts, which is the trap: an unrelated file that happens to open
with three lines of text and a row of numbers would then be described as a
molecule it is not. Report, never guess.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence

#: How many lines a molfile fixes before the connection table starts, and the
#: index of the counts line among them.
HEADER_LINES = 4
COUNTS_LINE = 3

#: The two layouts, and the literal each writes onto its counts line. Matched as
#: a substring rather than at columns 34-39: writers pad the counts line to
#: several widths, and no other token on that line can be mistaken for one of
#: these.
V2000 = "V2000"
V3000 = "V3000"

#: Where V2000 keeps its two counts, as half-open column slices.
ATOM_COLUMNS = slice(0, 3)
BOND_COLUMNS = slice(3, 6)

#: The line a V3000 file states its counts on, and where the two counts sit
#: among the tokens of it: ``M  V30 COUNTS na nb nsg n3d chiral``.
COUNTS_PREFIX = "M  V30 COUNTS"
V3000_ATOM_TOKEN = 3
V3000_BOND_TOKEN = 4

#: The line closing the connection table, and the last line of a molfile block
#: wherever one sits. Everything above it belongs to the molecule, the free
#: text of the title lines included; whatever a container writes below it is
#: the container's own.
END_MARKER = "M  END"

#: How much of the head a caller has to offer for the version literal to be
#: readable. The three lines above the counts line are 80 characters each by
#: specification, so this is an order of magnitude more than a molfile needs and
#: still nothing next to a file.
CLAIM_BYTES = 4096


@dataclass(frozen=True)
class MolfileHeader:
    """What the first four lines of a molfile state, and all of it.

    ``title`` is what the depositor typed and may be empty; a molfile with no
    title is still a molfile, and the two other fields are the structure.
    """

    title: str
    version: str
    atom_count: int
    bond_count: int


def head_declares_version(head: bytes) -> bool:
    """Whether the fourth line of ``head`` carries a molfile version literal.

    The claim test both handlers share. Decoded permissively: a molfile is
    printable ASCII by specification, and a stray byte in a title or a comment
    is not a reason to refuse to look at the counts line below it.
    """
    lines = head.decode("utf-8", "replace").split("\n")
    if len(lines) <= COUNTS_LINE:
        return False
    return _version_of(lines[COUNTS_LINE]) is not None


def parse_molfile_header(lines: Sequence[str]) -> MolfileHeader:
    """The title, version and counts of one molfile block.

    Reads only as far as the counts: the fourth line for a V2000 block, and for
    a V3000 block the ``COUNTS`` line below it. Everything after that is the
    connection table, which is the molecule rather than a statement about it.

    Args:
        lines: The block's lines, without their endings. A caller reading a
            bounded prefix passes what it has; a header that does not complete
            inside it is refused, which is what the caller wants to be told.

    Raises:
        ValueError: If the block ends before its counts, if the counts line
            carries neither version literal, or if the counts themselves do not
            parse. The message is a continuation naming what was wrong, so a
            caller can prefix it with the file it was reading.
    """
    if len(lines) < HEADER_LINES:
        raise ValueError(
            f"ends after {len(lines)} lines, before the counts line the format "
            f"puts fourth"
        )
    counts = lines[COUNTS_LINE]
    version = _version_of(counts)
    if version is None:
        raise ValueError(
            f"carries neither a {V2000} nor a {V3000} literal on its counts "
            "line, so it states no molfile layout to read the counts under"
        )
    title = lines[0].strip()
    if version == V2000:
        return MolfileHeader(title, version, *_v2000_counts(counts))
    return MolfileHeader(title, version, *_v3000_counts(lines))


def _version_of(counts: str) -> Optional[str]:
    """The layout a counts line declares, or None if it declares neither."""
    for version in (V2000, V3000):
        if version in counts:
            return version
    return None


def _v2000_counts(counts: str) -> tuple:
    """The atom and bond counts of a V2000 counts line, off its columns."""
    try:
        return int(counts[ATOM_COLUMNS]), int(counts[BOND_COLUMNS])
    except ValueError as exc:
        raise ValueError(
            f"declares {V2000} but its counts line holds "
            f"{counts[ATOM_COLUMNS]!r} and {counts[BOND_COLUMNS]!r} where the "
            "atom and bond counts belong"
        ) from exc


def _v3000_counts(lines: Sequence[str]) -> tuple:
    """The atom and bond counts of a V3000 block, off its ``COUNTS`` line."""
    for line in lines[HEADER_LINES:]:
        if not line.startswith(COUNTS_PREFIX):
            continue
        tokens: List[str] = line.split()
        try:
            return (
                int(tokens[V3000_ATOM_TOKEN]),
                int(tokens[V3000_BOND_TOKEN]),
            )
        except (IndexError, ValueError) as exc:
            raise ValueError(
                f"declares {V3000} but its {COUNTS_PREFIX!r} line reads "
                f"{line!r}, which names no atom and bond count"
            ) from exc
    raise ValueError(
        f"declares {V3000} but states no {COUNTS_PREFIX!r} line, where that "
        "layout keeps the counts its own counts line leaves at zero"
    )
