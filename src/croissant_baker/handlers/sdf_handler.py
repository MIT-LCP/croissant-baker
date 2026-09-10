"""SDF handler: the schema a compound library states in its own data items.

An SD file is molfile blocks concatenated, each followed by the depositor's own
annotations and closed by a ``$$$$`` terminator. Those annotations are the
schema: a header line naming the field between angle brackets, the value on the
lines below it, and a blank line closing it. Repeated across a library they are
columns, which is what makes an SDF a table where a lone molfile is not.

Nothing declares those columns up front, so they are read off the records, and
reading every record of a screening library means reading the whole file. The
sample is the compromise: the first :data:`SAMPLE_RECORDS` records or
:data:`SAMPLE_BYTES` bytes, whichever ends first, and the record set says in its
own description how many records its field list came from. That is the same
bargain the JSON handler strikes over ``SCHEMA_SAMPLE``.

No ``extract`` on any field: Croissant 1.1's extract grammar addresses columns
of a format mlcroissant's reader knows how to open, and SDF is not on that list,
so a column reference here would be a promise nobody can keep. Every field is
sourced from the file and nothing narrower, as VCF's are.
"""

import re
from typing import Dict, List, Optional, Sequence, Tuple

import mlcroissant as mlc

from croissant_baker.handlers import molfile
from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.utils import (
    allocate_record_set_ids,
    display_name,
    infer_croissant_type,
    make_field_id,
    plural,
    read_prefix_chunks,
)
from croissant_baker.sources import UNREADABLE, FileSource

#: The chemical MIME family, which is not IANA-registered but is what chemistry
#: toolkits, journals and structure databases have served SD files as for
#: decades. It is the media type a consumer of this metadata will recognise.
ENCODING_FORMAT = "chemical/x-mdl-sdfile"

#: The line closing one record, and the only marker in the format that says
#: where a record ends.
RECORD_TERMINATOR = "$$$$"

#: The character a data item's header line opens with.
ITEM_MARKER = ">"

#: How much of a library is sampled for the field list, and the two bounds are
#: both needed. The record bound is what makes the cost the same for a library
#: of ten million compounds as for one of a hundred; the byte bound is what
#: stops a file that never writes a terminator from being read to its end while
#: the record bound waits for a record that does not arrive.
SAMPLE_RECORDS = 100
SAMPLE_BYTES = 4 * 1024 * 1024

#: What a value must look like for its type to be read off it. Regexes rather
#: than a bare ``int()`` or ``float()``, which accept spellings a data item is
#: not: ``int("1_0")`` is ten, and ``float(" 1 ")`` is one, so a value the file
#: writes as text would be typed as a number on a Python parsing quirk.
_INTEGER = re.compile(r"[+-]?\d+\Z")
_DECIMAL = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\Z")

#: The suffix the one record set per file is allocated under.
RECORD_SET_SUFFIX = "molecules"

#: The two fields every record has that no data item names: the molecule's own
#: title line, and the connection table below it.
STRUCTURE_FIELDS = (
    ("title", "Molecule name, from the first line of the molfile block"),
    (
        "molfile",
        "The molfile block: the connection table as text, from the title line "
        "down to its 'M  END'",
    ),
)

#: What a version is called when the sample holds more than one of them.
MIXED_VERSION = "mixed"


def _decode(raw: bytes) -> str:
    """One line as text, with the carriage return of a CRLF file gone.

    Decoded permissively: an SD file is printable ASCII by specification, and a
    stray byte in a data item is not a reason to refuse a file whose structure
    is otherwise readable.
    """
    return raw.decode("utf-8", "replace").rstrip("\r")


def _take_line(line: str, records: List[List[str]], current: List[str]) -> List[str]:
    """Fold one line into the record being read, and hand back the list the
    next line goes into.

    The terminator closes the record rather than joining it, so what is
    returned after one is the empty list the next record is read into.
    """
    if line.strip() == RECORD_TERMINATOR:
        records.append(current)
        return []
    current.append(line)
    return current


def item_name(line: str) -> Optional[str]:
    """The field a data item header names, or None if the line is not one.

    The name is the text inside the first ``<...>``. The decorations a header
    line may carry around it — an external registry number, a ``DT`` field code
    — are deliberately not read: they name the field's provenance in the
    depositor's own system, not its place in this schema.
    """
    if not line.startswith(ITEM_MARKER):
        return None
    start = line.find("<")
    end = line.find(">", start + 1)
    if start < 0 or end < 0:
        return None
    return line[start + 1 : end]


def data_items(lines: Sequence[str]) -> List[Tuple[str, str]]:
    """The data items of one record, in the order the record writes them.

    A value runs from the line below its header to the blank line closing it,
    and may span several lines. Lines of the molfile block above are passed
    over: none of them opens with ``>``, which is what makes the scan safe to
    run over the whole record rather than only the part below ``M  END``.
    """
    items: List[Tuple[str, str]] = []
    index = 0
    while index < len(lines):
        name = item_name(lines[index])
        if name is None:
            index += 1
            continue
        index += 1
        value: List[str] = []
        while index < len(lines) and lines[index].strip():
            value.append(lines[index])
            index += 1
        items.append((name, "\n".join(value)))
    return items


def value_type(value: str) -> Optional[str]:
    """The Croissant type of one data item value, or None if it is not a number.

    Every value in an SD file is text on disk, so the number is parsed first and
    :func:`~croissant_baker.handlers.utils.infer_croissant_type` is asked about
    the parsed value rather than the string. Asking it about the string would
    type a compound name that happens to look like a date, or an identifier that
    happens to open with ``urn:``, as something the format never declared it to
    be.
    """
    if _INTEGER.match(value):
        return infer_croissant_type(int(value))
    if _DECIMAL.match(value):
        return infer_croissant_type(float(value))
    return None


def field_type(values: Sequence[str]) -> str:
    """The type every sampled value of one field agrees on.

    Agreement rather than a majority vote: a field is a number only if nothing
    in the sample says otherwise, because a consumer that reads a column as
    numeric and meets a name in it has been told something untrue. A value
    spanning several lines is text whatever those lines hold. Empty values are
    passed over as missing rather than counted as text.
    """
    types = set()
    for value in values:
        if not value:
            continue
        if "\n" in value:
            return "sc:Text"
        inferred = value_type(value)
        if inferred is None:
            return "sc:Text"
        types.add(inferred)
    if not types:
        return "sc:Text"
    return types.pop() if len(types) == 1 else "cr:Float64"


class SDFHandler(FileTypeHandler):
    """Handler for MDL structure-data files (``.sdf``, ``.sd``).

    One RecordSet per file, whose fields are the molecule's title, its
    connection table, and one per data item the sampled records carry. The
    molfile blocks are read only as far as their counts, and the read stops at
    the end of the sample rather than at the end of the file.
    """

    EXTENSIONS = (".sdf", ".sd")
    FORMAT_NAME = "SDF"
    ENCODING_FORMAT = ENCODING_FORMAT
    FORMAT_DESCRIPTION = (
        "Molfile version and data field names with types, inferred from a "
        "bounded sample of records"
    )

    def claims(self, source: FileSource) -> bool:
        """Claim a declared spelling whose head is a molfile block or a record.

        The extension is not evidence on its own: ``.sdf`` is also a spatial
        data format and more than one tool's session file. Either marker below
        it is, and both are offered because either can be the one in reach: a
        library opens with a molfile block, so its fourth line declares a
        version, while a file whose first block this handler cannot read may
        still carry the ``$$$$`` terminator that no other text format writes.

        A file that cannot be read peeks as ``b""`` and is therefore not
        claimed, corrupt wrappers included; that is
        :meth:`~croissant_baker.sources.FileSource.peek`'s contract, and no
        handler repeats it.
        """
        if source.suffix not in self.EXTENSIONS:
            return False
        head = source.peek(molfile.CLAIM_BYTES)
        if molfile.head_declares_version(head):
            return True
        return RECORD_TERMINATOR.encode() in head

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Read the first records of the file, and stop where the sample ends."""
        if not source.exists:
            raise FileNotFoundError(
                f"{self.FORMAT_NAME} file not found: {source.relative_path}"
            )

        name = str(source.relative_path)
        records, exhausted = self._read_records(source, name)
        versions = self._versions(records, name)
        fields = self._fields(records)

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": self.ENCODING_FORMAT,
            "molfile_version": (
                versions[0] if len(set(versions)) == 1 else MIXED_VERSION
            ),
            "sampled_records": len(records),
            "sample_exhausted": exhausted,
            "fields": fields,
        }

    # ------------------------------------------------------------------

    def _read_records(
        self, source: FileSource, name: str
    ) -> Tuple[List[List[str]], bool]:
        """The sampled records, and whether the file ended inside the sample.

        Taken a chunk at a time rather than a line at a time: a stream iterated
        by line hands back the whole file as one line when the file holds no
        line ending, and reading the whole file is the one thing the sample
        exists to avoid. The loop stops as soon as either bound is reached, so
        nothing past the sample is pulled off the stream at all.

        A file that ends without a line ending behind its last line is read to
        its end all the same: the tail is the last line, and a writer that
        closes the file straight after the terminator leaves it there.
        """
        records: List[List[str]] = []
        current: List[str] = []
        pending = b""
        read = 0
        bounded = ""
        try:
            with source.open() as stream:
                for chunk in read_prefix_chunks(stream, SAMPLE_BYTES + 1):
                    read += len(chunk)
                    if read > SAMPLE_BYTES:
                        chunk = chunk[: len(chunk) - (read - SAMPLE_BYTES)]
                        bounded = f"the first {SAMPLE_BYTES} bytes"
                    complete = (pending + chunk).split(b"\n")
                    # The tail after the last line ending is not yet a line.
                    pending = complete.pop()
                    for raw in complete:
                        current = _take_line(_decode(raw), records, current)
                    if len(records) >= SAMPLE_RECORDS:
                        bounded = bounded or plural(SAMPLE_RECORDS, "record")
                    if bounded:
                        break
                if pending and not bounded:
                    # End of file with no line ending behind the last line,
                    # which is where a writer that closes the file straight
                    # after the terminator leaves it.
                    _take_line(_decode(pending), records, current)
        except UNREADABLE as exc:
            raise ValueError(
                f"Failed to read {self.FORMAT_NAME} file {name}: {exc}"
            ) from exc

        if not records:
            raise ValueError(
                f"Not an {self.FORMAT_NAME} file: {name} states no complete "
                f"molecule record"
                + (
                    f" within {bounded}, which is as far as this handler reads"
                    if bounded
                    else f", because it carries no '{RECORD_TERMINATOR}' terminator"
                )
            )
        # A record left open where the sample ended was never terminated, so
        # what was read of it is a fragment rather than a record.
        return records[:SAMPLE_RECORDS], not bounded

    def _versions(self, records: List[List[str]], name: str) -> List[str]:
        """The molfile layout each sampled record declares.

        Every record, not only the first: a concatenation is free to mix
        layouts, and a record whose header this handler cannot read is one whose
        block it cannot describe, so the file is reported rather than described
        from the records that happened to parse.
        """
        versions = []
        for index, lines in enumerate(records, start=1):
            try:
                versions.append(molfile.parse_molfile_header(lines).version)
            except ValueError as exc:
                raise ValueError(
                    f"Not an {self.FORMAT_NAME} file: record {index} of {name} {exc}"
                ) from exc
        return versions

    def _fields(self, records: List[List[str]]) -> List[Dict[str, str]]:
        """One entry per data item name, in the order the sample first saw it.

        First-seen order rather than sorted: it is the order the depositor wrote
        the items in, it is the same on every run over the same bytes, and it is
        the order a reader opening the file sees them.
        """
        sampled: Dict[str, List[str]] = {}
        for lines in records:
            for item, value in data_items(lines):
                sampled.setdefault(item, []).append(value)
        return [
            {"name": item, "type": field_type(values)}
            for item, values in sampled.items()
        ]

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """One record set per library: its molecules, as the sample found them."""
        allocated = allocate_record_set_ids(file_metas, [RECORD_SET_SUFFIX])
        record_sets = [
            self._record_set(meta, file_id, ids[RECORD_SET_SUFFIX])
            for meta, file_id, ids in zip(file_metas, file_ids, allocated)
        ]
        return BuildResult([], record_sets)

    def _record_set(self, meta: dict, file_id: str, rs_id: str) -> mlc.RecordSet:
        used: set = set()
        fields = [
            self._field(rs_id, file_id, used, name, "sc:Text", description)
            for name, description in STRUCTURE_FIELDS
        ]
        fields += [
            self._field(
                rs_id,
                file_id,
                used,
                item["name"],
                item["type"],
                f"Data item '{item['name']}', typed from the sampled values",
            )
            for item in meta["fields"]
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
        name: str,
        data_type: str,
        description: str,
    ) -> mlc.Field:
        """One field, sourced from the file and nothing narrower."""
        return mlc.Field(
            id=make_field_id(parent_id, name, used),
            name=name,
            description=description,
            data_types=[data_type],
            source=mlc.Source(file_object=file_id),
        )


def _description(meta: dict) -> str:
    """What the sample found, in one deterministic sentence.

    The record count the fields came from is stated rather than left implied:
    a field list read off a hundred records of a million is a statement about
    those hundred, and a reader is owed the difference.
    """
    read = (
        f"from all {plural(meta['sampled_records'], 'record')}"
        if meta["sample_exhausted"]
        else f"from the first {plural(meta['sampled_records'], 'record')}"
    )
    stated = [
        meta["molfile_version"],
        f"{plural(len(meta['fields']), 'data field')}, {read}",
    ]
    return (
        f"Molecule records in {display_name(meta)} ({'; '.join(stated)}). "
        "One record per molecule."
    )
