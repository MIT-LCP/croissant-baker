"""What a CIF or STAR document holds, in the terms Croissant needs.

CIF and STAR share one grammar: a document is a sequence of data blocks, and a
block holds tag/value pairs, loops, or both. gemmi parses that grammar; this
module says what the parse means for a dataset description, so the handlers for
the several formats built on it agree on blocks, columns and types rather than
each inventing an answer.

Nothing here knows about Croissant nodes. It returns :class:`Table`
descriptions; a handler turns those into record sets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Tuple

import gemmi

#: Rows read per column when inferring its type. A refined particle stack has
#: millions of rows and every one of them costs a Python-level call, so the head
#: of the column decides: a value below the bound that contradicts the rest is
#: not seen, and the column keeps the type its head implies.
MAX_SAMPLE_ROWS = 1000

#: The two shapes a block can take. A block holding both yields one table of
#: each, because a pair is one value for the block and a loop row is one record.
KIND_PAIRS = "pairs"
KIND_LOOP = "loop"

#: What a block with no name after ``data_`` is called, so a table always has a
#: name an identifier can be derived from.
UNNAMED_BLOCK = "block"

INTEGER = "sc:Integer"
FLOAT = "sc:Float"
TEXT = "sc:Text"

# Deliberately narrower than int() and float(), which accept spellings CIF does
# not have: int() reads "1_000" as a thousand, and float() reads "nan" and "inf"
# as numbers where a file means those literally.
_INTEGER = re.compile(r"[+-]?\d+$")
_FLOAT = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


@dataclass(frozen=True)
class Table:
    """One table a document holds: a loop, or a block's pairs taken together.

    Attributes:
        block: The name of the data block it came from, without ``data_``.
        name: A name unique within the document, so an identifier derived from
            it stays unique too.
        kind: :data:`KIND_LOOP` or :data:`KIND_PAIRS`.
        columns: ``(column name, Croissant type)`` in the order the file
            declares them.
        rows: The loop's length, or 1 for a block's pairs.
    """

    block: str
    name: str
    kind: str
    columns: Tuple[Tuple[str, str], ...]
    rows: int


def describe(document) -> List[Table]:
    """Every table in a parsed document, in file order.

    Args:
        document: A ``gemmi.cif.Document``, from ``gemmi.cif.read_string`` or
            any of gemmi's other readers.

    Returns:
        One :class:`Table` per loop, plus one per block that carries pairs. A
        block with no items at all yields nothing: there is no column to
        describe, and a record set without fields is one mlcroissant refuses.
    """
    tables: List[Table] = []
    used: set = set()
    for block in document:
        found = _tables_of(block)
        for name, (kind, columns, rows) in zip(_names(block.name, len(found)), found):
            tables.append(
                Table(
                    block=block.name,
                    name=_unique(name, used),
                    kind=kind,
                    columns=columns,
                    rows=rows,
                )
            )
    return tables


def _tables_of(block) -> list:
    """``(kind, columns, rows)`` per table in one block, pairs first."""
    pairs = []
    loops = []
    for item in block:
        if item.loop is not None:
            loops.append(item.loop)
        elif item.pair is not None:
            pairs.append(item.pair)

    found = []
    if pairs:
        columns = tuple(
            (_column_name(tag), _column_type([value])) for tag, value in pairs
        )
        found.append((KIND_PAIRS, columns, 1))
    for loop in loops:
        found.append((KIND_LOOP, _loop_columns(loop), loop.length()))
    return found


def _loop_columns(loop) -> Tuple[Tuple[str, str], ...]:
    """One ``(name, type)`` per loop tag, typed from the head of its column."""
    sampled = min(loop.length(), MAX_SAMPLE_ROWS)
    return tuple(
        (
            _column_name(tag),
            _column_type(loop[row, column] for row in range(sampled)),
        )
        for column, tag in enumerate(loop.tags)
    )


def _column_name(tag: str) -> str:
    """``_rlnImageName`` becomes ``rlnImageName``.

    Only the leading underscore goes. A dot inside the tag separates an mmCIF
    category from its keyword and belongs to the name: ``_struct.title`` names
    one column, not two.
    """
    return tag[1:] if tag.startswith("_") else tag


def _column_type(values: Iterable[str]) -> str:
    """The Croissant type every value in the column fits.

    The null tokens ``.`` (inapplicable) and ``?`` (unknown) say nothing about
    the type and are skipped, so one missing measurement does not turn a column
    of floats into text. A column of nothing but nulls is text, which claims the
    least.
    """
    seen = False
    integral = True
    for raw in values:
        if gemmi.cif.is_null(raw):
            continue
        value = gemmi.cif.as_string(raw)
        seen = True
        if not _FLOAT.match(value):
            return TEXT
        if integral and not _INTEGER.match(value):
            integral = False
    if not seen:
        return TEXT
    return INTEGER if integral else FLOAT


def _names(block: str, count: int) -> List[str]:
    """Names for one block's tables, indexed only where there is a choice."""
    base = block or UNNAMED_BLOCK
    if count == 1:
        return [base]
    return [f"{base}_{i}" for i in range(1, count + 1)]


def _unique(name: str, used: set) -> str:
    """``name``, or the first free ``name__N``.

    A document may repeat a block name; the identifiers a handler derives from
    these names may not repeat, so the collision is settled once, here.
    """
    candidate = name
    n = 2
    while candidate in used:
        candidate = f"{name}__{n}"
        n += 1
    used.add(candidate)
    return candidate
