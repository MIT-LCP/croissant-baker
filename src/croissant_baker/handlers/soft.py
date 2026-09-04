"""A streaming reader for the GEO SOFT line grammar.

Standard library only, and nothing from the rest of the tree. The grammar is at
https://www.ncbi.nlm.nih.gov/geo/info/soft.html; what the baker does with the
result is in ``docs/user-guide/supported-formats.md``.

One fact about the format sets the shape of the code: **the tables are the
file.** 99.96 % of a 50 MiB family export lies between ``!*_table_begin`` and
``!*_table_end``, most of it in a single platform annotation table at ~2 KiB a
row. Rows are sampled up to a bound per column signature and then skipped, so
peak memory is set by the sample rather than by the deposit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

#: Rows sampled per distinct table signature, for the caller's type inference.
SAMPLE_ROWS = 500

#: A ceiling on that same sample, because a row is not a bounded thing: GPL96
#: runs ~2 KiB a row, so 500 rows is already 1 MiB. The header is kept whatever
#: its size, which is the one way the buffer exceeds this.
SAMPLE_BYTES = 1 << 20

#: A whole marker line and nothing else. ``\w`` admits no ``=``, space or tab,
#: which is what keeps ordinary content from opening or closing a table.
_TABLE_MARKER = re.compile(r"!\w+_table_(begin|end)", re.IGNORECASE)

#: ``!Sample_characteristics_ch1`` once its ``Sample_`` prefix has come off.
_CHARACTERISTICS = re.compile(r"^characteristics(?:_ch(\w+))?$", re.IGNORECASE)

#: ``!Sample_data_row_count = 22283``, likewise prefix-stripped. Every sample of
#: every real export tested declares one, so no table body is ever counted.
_ROW_COUNT = "data_row_count"

#: What separates a characteristic's key from its value. The literal sequence,
#: not a bare colon: a bare colon makes a key of ``stage:`` and refuses the
#: namespaced ``efo:cell type: fibroblast``, whose key carries one of its own.
_KEY_SEPARATOR = ": "

#: What a partial parse can mean. An attribute block has no closing marker, so a
#: file ending after a complete attribute line is *not* partial.
TABLE_UNCLOSED = "a data table was still open at end of file"
UNDECODABLE = "{count} line(s) could not be decoded"


@dataclass
class FieldGroup:
    """The field names one record set would carry, and their cardinality.

    ``names`` maps each name to whether it repeated *inside a single entity* —
    two ``!Sample_description`` lines on one sample are one field with two
    values. Insertion order is the deposit's declaration order.
    """

    entities: int = 0
    names: dict = field(default_factory=dict)

    def observe(self, name: str, repeated: bool) -> None:
        self.names[name] = self.names.get(name, False) or repeated


@dataclass
class Table:
    """One column signature, and every entity that declared it.

    ``rows`` is summed from the ``!*_data_row_count`` declarations and is
    trustworthy only when ``rows_declared`` reaches ``entities``.
    """

    kind: str
    columns: tuple
    #: Column name -> its ``#COLUMN`` line, verbatim. First non-empty one wins.
    column_lines: dict = field(default_factory=dict)
    entities: int = 0
    rows: int = 0
    rows_declared: int = 0
    #: Header plus a bounded row sample, as UTF-8 TSV, for the caller to type.
    #: Filled across every entity of this signature until the bound is spent,
    #: because a deposit may declare an empty table before a populated one. A
    #: ``bytearray`` since ``bytes += bytes`` copies the whole buffer per row.
    sample: bytearray = field(default_factory=bytearray, repr=False)
    _sampled_rows: int = field(default=0, repr=False)

    @property
    def rows_known(self) -> bool:
        """Whether every contributing entity declared its row count."""
        return self.entities > 0 and self.rows_declared == self.entities


@dataclass
class SoftFile:
    """What one SOFT export declares.

    ``kinds`` holds only the entity kinds present: the parser reports every kind
    it read and leaves to its caller which to describe.
    """

    kinds: dict = field(default_factory=dict)
    characteristics: FieldGroup = field(default_factory=FieldGroup)
    #: Characteristics lines that were not ``key: value``. The convention is a
    #: convention, not a grammar.
    unparsed: int = 0
    #: The attribute names those lines arrived on, mapped to whether they
    #: repeated within one entity. They become the fallback fields.
    fallbacks: dict = field(default_factory=dict)
    #: One per distinct ``(kind, columns)`` signature, in declaration order.
    tables: list = field(default_factory=list)
    #: The conditions that made this a best-effort read, if any.
    incomplete: tuple = ()


def parse(
    lines: Iterable[bytes],
    *,
    sample_rows: int = SAMPLE_ROWS,
    sample_bytes: int = SAMPLE_BYTES,
) -> SoftFile:
    """Read one SOFT export in a single forward pass.

    Args:
        lines: The decompressed file, iterated as raw byte lines.
        sample_rows: Table rows to buffer per distinct column signature.
        sample_bytes: A ceiling on that same buffer.

    Returns:
        The deposit's shape. An input with no ``^`` entity line yields no kinds,
        which is how a caller tells "not SOFT" from "SOFT that says little"; the
        parser does not raise, because it does not know what file it is reading.
    """
    state = _State(sample_rows, sample_bytes)
    for raw in lines:
        state.feed(raw)
    return state.finish()


class _State:
    """The forward pass. One instance per file, never shared."""

    def __init__(self, sample_rows: int, sample_bytes: int) -> None:
        self._sample_rows = sample_rows
        self._sample_bytes = sample_bytes
        self._out = SoftFile()
        self._undecodable = 0

        # The open entity.
        self._kind: Optional[str] = None
        self._prefix = ""
        self._counts: dict = {}
        self._char_counts: dict = {}
        self._fallback_counts: dict = {}
        self._column_lines: dict = {}
        self._declared_rows: Optional[int] = None

        # The open table. ``_table`` is None while its header is still awaited.
        self._in_table = False
        self._table: Optional[Table] = None

        self._characteristics: dict = {}
        self._channels: set = set()
        # Signature -> Table, so a later entity declaring the same columns joins
        # the first rather than starting a record set of its own.
        self._by_signature: dict = {}

    # ------------------------------------------------------------------

    def feed(self, raw: bytes) -> None:
        """Consume one raw line."""
        line = self._decode(raw)
        marker = _table_marker(line)

        if self._in_table:
            if marker == "end":
                self._close_table()
            elif self._table is None:
                self._start_table(line)
            else:
                self._sample_row(line)
            return

        if marker is not None:
            # A begin marker opens a table; a stray end marker is neither a
            # table nor an attribute, so it is dropped rather than named.
            if marker == "begin" and self._kind is not None:
                self._in_table = True
                self._table = None
            return
        if not line:
            return
        if line[0] == "^":
            self._open_entity(line[1:])
        elif line[0] == "!":
            self._attribute(line[1:])
        elif line[0] == "#":
            self._column_line(line)

    def finish(self) -> SoftFile:
        """Report what the pass found, and whether it reached the end."""
        incomplete = []
        if self._in_table:
            incomplete.append(TABLE_UNCLOSED)
        if self._undecodable:
            incomplete.append(UNDECODABLE.format(count=self._undecodable))
        self._out.incomplete = tuple(incomplete)
        self._name_characteristics()
        return self._out

    # ------------------------------------------------------------------
    # Lines
    # ------------------------------------------------------------------

    def _decode(self, raw: bytes) -> str:
        """One line as text, having counted it if a byte would not decode.

        GEO declares no encoding. Every real export tested is valid UTF-8 — the
        ``37ºC`` in a 2004 deposit is ``0xC2 0xBA``, and latin-1 is what
        corrupts it to ``37ÂºC`` — but the format guarantees nothing.
        """
        try:
            return raw.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError:
            self._undecodable += 1
            return raw.decode("utf-8", "replace").rstrip("\r\n")

    def _open_entity(self, rest: str) -> None:
        kind = rest.partition("=")[0].strip().upper()
        self._kind = kind
        self._prefix = f"{kind.lower()}_"
        self._counts = {}
        self._char_counts = {}
        self._fallback_counts = {}
        self._column_lines = {}
        self._declared_rows = None
        self._out.kinds.setdefault(kind, FieldGroup()).entities += 1

    def _attribute(self, rest: str) -> None:
        if self._kind is None:
            return  # a SOFT file opens with ^DATABASE before any !

        raw_name, _, value = rest.partition("=")
        name = self._strip_prefix(raw_name.strip())
        if not name:
            return  # `! = value` names nothing, and a field needs a name

        if name.lower() == _ROW_COUNT:
            # Still an attribute, and so still a field: a name the deposit uses
            # does not stop being one because the parser reads its value.
            self._declared_rows = _as_int(value.strip())

        channel = _characteristic_channel(name)
        if channel is not None and self._kind == "SAMPLE":
            self._characteristic(name, channel, value.lstrip())
            return

        count = self._counts[name] = self._counts.get(name, 0) + 1
        self._out.kinds[self._kind].observe(name, count > 1)

    def _characteristic(self, attribute: str, channel: str, value: str) -> None:
        # The channel is in the attribute name, so it counts whatever the value
        # turned out to be. Otherwise a malformed _ch2 line would leave every
        # valid channel-1 key unprefixed in a file that carries two channels.
        self._channels.add(channel)

        # ``diagnosis: `` still names ``diagnosis``, which is why the value is
        # not stripped before the split: a submitter leaving one spreadsheet
        # column empty still named the key.
        key, sep, _ = value.partition(_KEY_SEPARATOR)
        if not sep or not key.strip():
            self._out.unparsed += 1
            count = self._fallback_counts[attribute] = (
                self._fallback_counts.get(attribute, 0) + 1
            )
            was = self._out.fallbacks.get(attribute, False)
            self._out.fallbacks[attribute] = was or count > 1
            return

        slot = (channel, key.strip())
        count = self._char_counts[slot] = self._char_counts.get(slot, 0) + 1
        was = self._characteristics.get(slot, False)
        self._characteristics[slot] = was or count > 1

    def _column_line(self, line: str) -> None:
        column, sep, description = line[1:].partition("=")
        column = column.strip()
        if column and sep and description.strip():
            self._column_lines[column] = line.strip()

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------

    def _start_table(self, header: str) -> None:
        columns = tuple(header.split("\t"))
        signature = (self._kind, columns)

        table = self._by_signature.get(signature)
        if table is None:
            table = Table(kind=self._kind or "", columns=columns)
            table.sample += header.encode("utf-8") + b"\n"
            self._by_signature[signature] = table
            self._out.tables.append(table)

        for column, line in self._column_lines.items():
            table.column_lines.setdefault(column, line)
        table.entities += 1
        if self._declared_rows is not None:
            table.rows += self._declared_rows
            table.rows_declared += 1
            # Consumed, so a second table under one entity — which GEO does not
            # write — reports no row count rather than counting it twice.
            self._declared_rows = None
        self._table = table

    def _sample_row(self, line: str) -> None:
        table = self._table
        if table is None or table._sampled_rows >= self._sample_rows:
            return
        row = line.encode("utf-8") + b"\n"
        if len(table.sample) + len(row) > self._sample_bytes:
            # Too big for what is left of the budget. Skipped rather than
            # final: a smaller row later — or from another entity of this
            # signature — is still evidence, and a signature left with only
            # its header types every numeric column as text.
            return
        table.sample += row
        table._sampled_rows += 1

    def _close_table(self) -> None:
        self._table = None
        self._in_table = False

    # ------------------------------------------------------------------

    def _strip_prefix(self, raw: str) -> str:
        """``Sample_title`` under a ``^SAMPLE`` becomes ``title``.

        Only the open entity's own prefix comes off, so a stray ``!Sample_foo``
        inside a ``^PLATFORM`` is not renamed to something never written.
        """
        if self._prefix and raw.lower().startswith(self._prefix):
            return raw[len(self._prefix) :] or raw
        return raw

    def _name_characteristics(self) -> None:
        """Turn the collected ``(channel, key)`` slots into field names.

        A one-channel deposit keeps the bare key. Once a second channel appears
        *every* key is prefixed, because which of two ``gender`` keys keeps the
        bare name must not depend on which was met first.
        """
        prefixed = len(self._channels) > 1
        group = self._out.characteristics
        group.entities = self._out.kinds.get("SAMPLE", FieldGroup()).entities
        for (channel, key), repeated in self._characteristics.items():
            group.observe(f"ch{channel}_{key}" if prefixed else key, repeated)
        for attribute, repeated in self._out.fallbacks.items():
            group.observe(attribute, repeated)


def _table_marker(line: str) -> Optional[str]:
    """``"begin"``, ``"end"``, or None if this line is not a whole marker.

    Spaces are forgiven, tabs are not: the line ending is already off, so what
    remains to strip is either padding around a marker or a TSV delimiter, and
    ``!sample_table_end\t`` is a two-column row whose second cell is empty.
    """
    match = _TABLE_MARKER.fullmatch(line.strip(" "))
    return match.group(1).lower() if match else None


def _characteristic_channel(name: str) -> Optional[str]:
    """The channel of a prefix-stripped characteristics attribute, or None.

    ``""`` for a channel-less ``!Sample_characteristics``, which GEO does not
    write but the grammar does not forbid.
    """
    match = _CHARACTERISTICS.match(name)
    return (match.group(1) or "") if match else None


def _as_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except ValueError:
        return None
