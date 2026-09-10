"""Shared utilities for file handlers."""

import gzip
import io
import logging
import re
import warnings
from pathlib import Path
from typing import (
    BinaryIO,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Sequence,
    Union,
)


import mlcroissant as mlc
import pyarrow as pa
import pyarrow.types as patypes

from croissant_baker.sources import hash_file

logger = logging.getLogger(__name__)

# Records sampled per file for schema inference in JSON-based handlers.
# Row counting always sees all records; only schema inference is capped.
# 500 covers typical field diversity while keeping memory bounded.
SCHEMA_SAMPLE = 500

# Croissant 1.1 array_shape: comma-separated dim sizes; -1 means "unknown".
# Examples: "-1" (1D unknown), "28,28" (fixed 2D), "-1,-1,3" (variable HxW, 3 channels).
# The mlcroissant 1.1.0 validator only accepts the bare comma-separated form
# (no parens, no trailing comma) — see normalize_array_shape() for the full
# rationale.
ARRAY_SHAPE_UNKNOWN_1D = "-1"


def normalize_array_shape(shape: str) -> str:
    """Coerce common shape spellings to the mlcroissant-accepted form.

    The validator parses array_shape by splitting on commas and casting each
    piece to int. So "(-1, -1)" or "(-1,)" — natural forms when stringifying
    a numpy.shape tuple — are rejected. This helper accepts both, returning
    a string the validator will accept.

    Examples:
        normalize_array_shape("-1")          -> "-1"
        normalize_array_shape("(-1,)")       -> "-1"
        normalize_array_shape("(-1, -1)")    -> "-1,-1"
        normalize_array_shape("28, 28")      -> "28,28"
    """
    inner = shape.strip().strip("()").rstrip(",").strip()
    return ",".join(part.strip() for part in inner.split(",") if part.strip())


def open_text_file(file_path: Path):
    """Deprecated. Read through :meth:`FileSource.open_text` instead.

    Kept so a handler written against the previous contract still imports and
    runs. It resolves compression the same way the pipeline does.
    """
    from croissant_baker import compression

    warnings.warn(
        "croissant_baker.handlers.utils.open_text_file is deprecated; read "
        "through FileSource.open_text(), which is already decompressed.",
        DeprecationWarning,
        stacklevel=2,
    )
    path = Path(file_path)
    comp = compression.compression_for(path.name)
    if comp is None:
        return open(path, "r", encoding=compression.DEFAULT_TEXT_ENCODING)
    return comp.opener(path, "rt", encoding=compression.DEFAULT_TEXT_ENCODING)


#: The largest header a handler will read out of a file that states its own
#: header length. Every such length is a number the file chooses, so trusting
#: one turns a header read into a read of the whole file, which is the one
#: thing a header-only handler exists not to do. 64 MiB is far above any real
#: header: a header of a million reference sequences, which no assembly has, is
#: a few tens of MiB, and a cohort declaring thousands of contigs and keys is a
#: few hundred KiB.
MAX_HEADER_BYTES = 64 * 1024 * 1024


def read_exactly(
    stream: BinaryIO, count: int, what: str, name: str, format_name: str
) -> bytes:
    """``count`` bytes, or a refusal naming the file and what was missing.

    Shared because every binary container reaches its header the same way: a
    length the file states, then that many bytes. A short read there is the
    file ending mid-header, and what a reader needs told is which file and
    which field, whichever container it was.
    """
    data = stream.read(count)
    if len(data) != count:
        raise ValueError(
            f"Truncated {format_name} header in {name}: {what} needs {count} "
            f"bytes, got {len(data)}"
        )
    return data


#: How much of a stream is pulled at a time by a handler reading a prefix whose
#: length nothing states in advance. Small enough that a short header costs one
#: read of it, large enough that a long one costs a handful.
PREFIX_CHUNK_BYTES = 32 * 1024


def read_prefix_chunks(
    stream: BinaryIO, limit: int, chunk_size: int = PREFIX_CHUNK_BYTES
) -> Iterator[bytes]:
    """Up to ``limit`` bytes of ``stream``, a chunk at a time, until it ends.

    Chunked rather than one read of ``limit``, because the limit is the size of
    the largest header or record anyone writes: pulling it every time would
    read a megabyte off a file to look at the first line of it. Chunked rather
    than iterated by line, because a file holding no line ending is one line,
    and reading it is reading the whole file.
    """
    remaining = limit
    while remaining > 0:
        data = stream.read(min(chunk_size, remaining))
        if not data:
            return
        remaining -= len(data)
        yield data


def decode_line(raw: bytes) -> str:
    """One line as text, with the carriage return of a CRLF file gone.

    Decoded permissively: the line-oriented formats in this tree are printable
    ASCII by specification, and a stray byte in a title, a comment or a data
    item is not a reason to refuse a file whose structure is otherwise
    readable.
    """
    return raw.decode("utf-8", "replace").rstrip("\r")


class PrefixLines:
    """The head of a stream as decoded lines, bounded in bytes.

    Chunked rather than iterated by line, because a stream iterated by line
    hands back the whole file as one line when the file holds no line ending,
    and reading the whole file is the one thing a bounded read exists not to
    do. One of these replaces the loop every line-oriented handler used to
    write out for itself.

    What follows the last line ending is delivered as a final line when the
    stream ended there: that is where a writer closing the file straight after
    its last line leaves it. It is dropped when the bound stopped the read
    instead, because a tail the bound cut in half is not a line and nothing may
    be read off it.

    ``on_chunk(read, pending)`` is called once per chunk, with the bytes pulled
    off the stream so far and the length of the line still being assembled, for
    a caller that owes the file a refusal before the line it is reading ends.
    """

    def __init__(
        self,
        stream: BinaryIO,
        limit: int,
        chunk_size: int = PREFIX_CHUNK_BYTES,
        on_chunk: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        self._stream = stream
        self._limit = limit
        self._chunk_size = chunk_size
        self._on_chunk = on_chunk
        #: Bytes pulled off the stream.
        self.read = 0
        #: Bytes of the line still being assembled.
        self.pending = 0
        #: Whether the bound stopped the read rather than the end of the
        #: stream. Answered once the iteration has run to its end; a caller
        #: that stops early stopped for a bound of its own.
        self.bounded = False

    def __iter__(self) -> Iterator[str]:
        pending = b""
        for chunk in read_prefix_chunks(self._stream, self._limit, self._chunk_size):
            self.read += len(chunk)
            complete = (pending + chunk).split(b"\n")
            # The tail after the last line ending is not yet a line.
            pending = complete.pop()
            self.pending = len(pending)
            for raw in complete:
                yield decode_line(raw)
            if self._on_chunk is not None:
                self._on_chunk(self.read, self.pending)
        self.bounded = self.read >= self._limit
        if pending and not self.bounded:
            yield decode_line(pending)
            self.pending = 0


def decompress_prefix(head: bytes, count: int) -> bytes:
    """The first ``count`` bytes inside a compressed prefix.

    A prefix, so the stream ends mid-member; that is expected, and the bytes
    already produced are the answer. Shared by the handlers whose format is
    itself a gzip container, so the compression layer hands them the bytes as
    they sit on disk and they open the wrapper themselves.
    """
    with gzip.GzipFile(fileobj=io.BytesIO(head), mode="rb") as payload:
        return payload.read(count)


def plural(count: int, noun: str) -> str:
    """``1 read group``, ``2 reference sequences``."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


# Characters that are invalid in Croissant @id values.
# mlcroissant rejects whitespace and URI-unsafe characters like >, (, ), %.
_INVALID_ID_CHARS = re.compile(r"[^A-Za-z0-9_.\-]")


def sanitize_id(raw: str) -> str:
    """Replace characters that mlcroissant rejects in @id values.

    Column names like 'Image Name' or 'Age>30(%)' contain spaces or
    URI-unsafe characters that cause mlcroissant validation errors.
    This replaces anything outside [A-Za-z0-9_.-] with underscores.
    """
    return _INVALID_ID_CHARS.sub("_", raw)


def _disambiguate_ids(items: list) -> list:
    """Return a list of unique @id strings parallel to ``items``.

    Each item is a ``(stem, parent_components)`` tuple, where ``stem`` is
    the desired sanitized identifier and ``parent_components`` is the
    list of parent path components (closest parent last) available for
    disambiguation. When two or more items share the same stem, the
    minimum number of trailing parent components is prepended (joined
    with ``__``) until every member of the colliding group is unique.

    Parents cannot separate stems that collide under one parent — two
    groupings of one directory, say — so a numeric suffix settles whatever
    survives. Uniqueness is the contract; callers assemble @ids from it.
    """
    from collections import defaultdict

    stems = [it[0] for it in items]
    parents_per = [it[1] for it in items]

    # Bucket items by their proposed stem; only buckets of size >1 need work.
    groups: dict = defaultdict(list)
    for i, stem in enumerate(stems):
        groups[stem].append(i)

    out = list(stems)
    for stem, indices in groups.items():
        if len(indices) == 1:
            continue  # no collision in this bucket; keep bare stem
        # Try increasing parent-prefix depths in lock-step across the colliding
        # bucket. The smallest depth at which every candidate is distinct wins.
        max_depth = max((len(parents_per[i]) for i in indices), default=0)
        chosen: dict = {}
        for depth in range(1, max_depth + 1):
            candidates: dict = {}
            for i in indices:
                parents = parents_per[i]
                prefix_parts = parents[-depth:] if depth <= len(parents) else parents
                prefix = "__".join(prefix_parts)
                candidates[i] = sanitize_id(f"{prefix}__{stem}") if prefix else stem
            if len(set(candidates.values())) == len(indices):
                chosen = candidates
                break
        if not chosen:
            chosen = {
                i: sanitize_id("__".join([*parents_per[i], stem])) for i in indices
            }
        for i, value in chosen.items():
            out[i] = value

    used: set = set()
    for i, value in enumerate(out):
        if value in used:
            n = 2
            while f"{value}__{n}" in used:
                n += 1
            value = f"{value}__{n}"
        used.add(value)
        out[i] = value
    return out


def make_record_set_ids(file_metas: list) -> list:
    """Return a unique RecordSet @id for each file in a handler's batch.

    The bare cleaned, sanitized basename is returned when no other file
    in the batch produces the same basename. When two or more files
    collide, parent-directory components from ``relative_path`` are
    prepended (joined with ``__``) up to the minimum depth that
    disambiguates the collision.

    The pattern follows the namespacing convention used by other
    Croissant generators (for example, the Hugging Face auto-generator
    prefixes config-level identifiers into split @id values), so
    consumers familiar with that style do not encounter a new shape.
    """
    items = [
        (
            sanitize_id(get_clean_record_name(meta["file_name"])),
            list(Path(meta.get("relative_path", meta["file_name"])).parts[:-1]),
        )
        for meta in file_metas
    ]
    return _disambiguate_ids(items)


#: Key under which :func:`allocate_record_set_ids` returns a file's own base
#: when asked for it. The empty string is not a suffix, so it cannot collide.
BASE = ""


def allocate_record_set_ids(
    file_metas: list, suffixes: Sequence[str], *, include_base: bool = False
) -> List[Dict[str, str]]:
    """One RecordSet @id per (file, suffix), unique across the whole batch.

    A handler that emits several record sets per file cannot use
    :func:`make_record_set_ids`, which returns one id per file and knows
    nothing about the names derived from it. Three steps, and the middle one
    is what a local implementation forgets:

    1. A base per file, from ``Path(file_name).stem`` plus parent components
       through :func:`_disambiguate_ids`, so two files with the same basename
       in different directories stay apart.
    2. **Every base is reserved**, so a real file named ``x_samples.csv`` keeps
       the bare ``x_samples`` and a record set derived from ``x.soft`` does not
       displace it.
    3. Each ``f"{base}_{suffix}"`` is allocated against that one set, so the
       derived ids cannot collide with each other either.

    ``Path.stem`` rather than :func:`get_clean_record_name`, whose extension
    list is hardcoded and holds neither ``.soft`` nor ``.jsonl``.

    Args:
        file_metas: One handler batch, in the handler's own order.
        suffixes: The suffixes to derive, applied to every file. A handler
            whose files need different subsets passes their union in a
            deterministic order and reads back only the ids it emits; a
            reserved id nothing uses costs a string and changes no other id,
            because every candidate is already prefixed by its own file's base.
        include_base: Also return each file's own base, under :data:`BASE`.
            For a handler that emits one unsuffixed record set for some of its
            files — the HDF5 handler does, for a container whose layout it did
            not recognise. Step 2 reserves the base either way, so asking for
            it moves no other id.

    Returns:
        One ``{suffix: @id}`` dict per file, parallel to ``file_metas``.
    """
    paths = [
        str(Path(meta.get("relative_path", meta["file_name"]))) for meta in file_metas
    ]
    items = [
        (
            sanitize_id(Path(meta["file_name"]).stem),
            list(Path(path).parts[:-1]),
        )
        for meta, path in zip(file_metas, paths)
    ]

    # Allocated in path order, not batch order. Batch order is rglob order, and
    # where parents cannot separate two stems — ``a b`` and ``a@b`` sanitize
    # alike — a numeric suffix settles it, so without this which file takes the
    # suffix would depend on which was discovered first.
    order = sorted(range(len(items)), key=lambda i: paths[i])
    bases = [""] * len(items)
    for base, i in zip(_disambiguate_ids([items[i] for i in order]), order):
        bases[i] = base

    taken = set(bases)
    allocated: List[Dict[str, str]] = [
        {BASE: base} if include_base else {} for base in bases
    ]
    for i in order:
        for suffix in suffixes:
            candidate = f"{bases[i]}_{sanitize_id(suffix)}"
            if candidate in taken:
                n = 2
                while f"{candidate}__{n}" in taken:
                    n += 1
                candidate = f"{candidate}__{n}"
            taken.add(candidate)
            allocated[i][suffix] = candidate
    return allocated


DIGIT_MASK = "<N>"

# A shard index stands on its own: it is either the whole stem or introduced by
# a separator. Digits fused to letters belong to a word instead — ``assay1`` and
# ``assay2`` are two tables, where ``part-00001`` is one table's shard.
_SHARD_INDEX = re.compile(r"(?:^|(?<=[-_.]))\d+")


def shard_template(file_name: str) -> Optional[str]:
    """The name with digit runs masked, or None if it carries no shard index.

    Shards of one table differ only in that index, so the masked name is the
    key they share. Only separated runs are masked: digits fused to letters
    name the table, so ``assay1-part-000`` and ``assay2-part-001`` stay two
    tables rather than collapsing into one.
    """
    if not _SHARD_INDEX.search(file_name):
        return None
    return _SHARD_INDEX.sub(DIGIT_MASK, file_name)


def make_field_id(record_set_id: str, column_name: str, used_field_ids: set) -> str:
    """Return a unique field @id within a single RecordSet.

    The candidate @id is ``{record_set_id}/{sanitize_id(column_name)}``.
    On collision (which happens when two distinct column names sanitize
    to the same string, for example ``Age>30`` and ``Age 30`` both
    becoming ``Age_30``), a numeric suffix ``__N`` is appended starting
    at 2, as in record-set identifier allocation.

    ``used_field_ids`` is mutated to record the chosen identifier so
    subsequent calls within the same RecordSet can detect further
    collisions.
    """
    base = f"{record_set_id}/{sanitize_id(column_name)}"
    if base not in used_field_ids:
        used_field_ids.add(base)
        return base
    n = 2
    while f"{base}__{n}" in used_field_ids:
        n += 1
    chosen = f"{base}__{n}"
    used_field_ids.add(chosen)
    return chosen


def map_arrow_type(arrow_type: pa.DataType) -> str:
    """
    Map a PyArrow data type to the corresponding Croissant type string.

    Uses precise Croissant types where available (cr:Int64, cr:Float32, etc.)
    and falls back to schema.org types for dates, text, and booleans.

    This is the single source of truth for type mapping across all handlers
    (CSV, Parquet, and future formats like JSON, O RC, Feather).

    Args:
        arrow_type: A PyArrow DataType from a table or file schema.

    Returns:
        Croissant-compatible type string (e.g. "sc:DateTime", "cr:Int64").
    """
    try:
        # Timestamps (with or without timezone) → sc:DateTime
        if patypes.is_timestamp(arrow_type):
            return "sc:DateTime"

        # Date-only (no time component) → sc:Date
        if patypes.is_date(arrow_type):
            return "sc:Date"

        # Time-only → sc:Time
        if patypes.is_time(arrow_type):
            return "sc:Time"

        # Integers — use precise Croissant types with bit-width
        if patypes.is_integer(arrow_type):
            prefix = "cr:UInt" if patypes.is_unsigned_integer(arrow_type) else "cr:Int"
            return f"{prefix}{arrow_type.bit_width}"

        # Floats — use precise Croissant types with bit-width
        # Croissant spec only defines cr:Float16, cr:Float32, cr:Float64.
        # For smaller widths (e.g. float8) fall back to generic sc:Float,
        # matching HuggingFace's behavior.
        if patypes.is_floating(arrow_type):
            bw = arrow_type.bit_width
            if bw in (16, 32, 64):
                return f"cr:Float{bw}"
            return "sc:Float"

        # Decimals → cr:Float64 (best general approximation)
        if patypes.is_decimal(arrow_type):
            return "cr:Float64"

        # Booleans
        if patypes.is_boolean(arrow_type):
            return "sc:Boolean"

        # Strings
        if patypes.is_string(arrow_type) or patypes.is_large_string(arrow_type):
            return "sc:Text"

        # Binary data
        if patypes.is_binary(arrow_type) or patypes.is_large_binary(arrow_type):
            return "sc:Text"

        # Null type (all values null) → safe fallback
        if patypes.is_null(arrow_type):
            return "sc:Text"

        # List / large-list / fixed-size-list: caller sets is_array=True; return the inner element type.
        if is_arrow_list(arrow_type):
            return map_arrow_type(arrow_type.value_type)

    except Exception:
        pass

    # Fallback for any unrecognized or exotic types (including struct — callers
    # that want nested sub_fields should detect is_struct before calling this).
    return "sc:Text"


def is_arrow_list(arrow_type: pa.DataType) -> bool:
    """Return True if the Arrow type is any list (variable, large, or fixed-size)."""
    return (
        patypes.is_list(arrow_type)
        or patypes.is_large_list(arrow_type)
        or patypes.is_fixed_size_list(arrow_type)
    )


def arrow_array_shape(arrow_type: pa.DataType) -> str:
    """Return the Croissant array_shape string for an Arrow list-like type.

    Fixed-size lists report their exact size (e.g. embedding columns of
    dimension 768). Variable-length lists fall back to the unknown-length
    sentinel since list lengths can differ row-to-row.
    """
    if patypes.is_fixed_size_list(arrow_type):
        return str(arrow_type.list_size)
    return ARRAY_SHAPE_UNKNOWN_1D


def infer_column_types_from_arrow_schema(schema: pa.Schema) -> Dict[str, str]:
    """
    Infer Croissant types for all columns in a PyArrow schema.

    This is the shared entry point used by both CSV and Parquet handlers.

    Args:
        schema: A PyArrow Schema (from a Table, ParquetFile, etc.)

    Returns:
        Dictionary mapping column names to Croissant type strings.
    """
    return {field.name: map_arrow_type(field.type) for field in schema}


def compute_file_hash(file_path: Union[str, Path]) -> str:
    """
    Compute SHA256 hash of a file for Croissant integrity verification.

    Reads the file as-is on disk (compressed bytes included) rather than
    decompressing first. This matches what users download and verify.

    Handlers take their own file's digest from ``source.sha256``. This stays
    for files a handler discovers itself, such as WFDB's sibling ``.dat`` and
    ``.atr``.

    Args:
        file_path: Path to the file (str or Path object)

    Returns:
        Hexadecimal SHA256 hash string

    Raises:
        FileNotFoundError: If the file doesn't exist
        PermissionError: If the file cannot be read
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if not file_path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")

    try:
        return hash_file(file_path)
    except PermissionError as e:
        raise PermissionError(f"Cannot read file {file_path}: {e}") from e


def _build_fields(
    arrow_schema,
    parent_id: str,
    source_ref: dict,
    col_path_prefix: str = "",
    used_field_ids: set = None,
) -> list:
    """Recursively build mlc.Field objects from a PyArrow schema or struct type.

    Handles three cases:
    - Scalar column: maps to a Croissant type via map_arrow_type().
    - List column: sets is_array=True; recurses on the element type.
    - Struct column: recurses to produce sub_fields.

    ``used_field_ids`` is an optional set of field @id values already
    emitted within the parent RecordSet; the function adds chosen ids
    to it so that columns whose names sanitize to the same string get a
    deterministic numeric suffix instead of silently colliding.
    """
    if used_field_ids is None:
        used_field_ids = set()
    fields = []
    for arrow_field in arrow_schema:
        col_name = arrow_field.name
        arrow_type = arrow_field.type
        field_id = make_field_id(parent_id, col_name, used_field_ids)
        col_path = f"{col_path_prefix}/{col_name}" if col_path_prefix else col_name

        is_array = is_arrow_list(arrow_type)
        inner_type = arrow_type.value_type if is_array else arrow_type

        source = mlc.Source(
            extract=mlc.Extract(column=col_path),
            **source_ref,
        )

        shape = arrow_array_shape(arrow_type) if is_array else None
        if patypes.is_struct(inner_type):
            sub_fields = _build_fields(inner_type, field_id, source_ref, col_path)
            field = mlc.Field(
                id=field_id,
                name=col_name,
                description=f"Column '{col_name}'",
                is_array=True if is_array else None,
                array_shape=shape,
                source=source,
                sub_fields=sub_fields,
            )
        else:
            col_type = map_arrow_type(inner_type)
            field = mlc.Field(
                id=field_id,
                name=col_name,
                description=f"Column '{col_name}'",
                data_types=[col_type],
                is_array=True if is_array else None,
                array_shape=shape,
                source=source,
            )
        fields.append(field)
    return fields


# ---------------------------------------------------------------------------
# JSON / FHIR type inference — shared by FHIRHandler and JSONHandler
# ---------------------------------------------------------------------------

_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")
_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_URL_PREFIXES = ("http://", "https://", "urn:")


def infer_croissant_type(value) -> str:
    """Map a scalar JSON value to a Croissant type string.

    Only handles primitives. Callers must unwrap dicts/lists before calling.
    """
    if isinstance(value, bool):
        return "sc:Boolean"
    if isinstance(value, int):
        return "cr:Int64"
    if isinstance(value, float):
        return "cr:Float64"
    if isinstance(value, str):
        if _DATETIME_RE.match(value):
            return "sc:DateTime"
        if _DATE_RE.match(value):
            return "sc:Date"
        if value.startswith(_URL_PREFIXES):
            return "sc:URL"
        return "sc:Text"
    return "sc:Text"


def infer_field_type(values: list):
    """Infer the type of a single JSON field from its sampled values.

    Returns one of:
    - a type string for scalar primitive fields (e.g. ``"sc:Date"``)
    - ``{"type": str, "is_array": True}`` for arrays of primitives
    - ``{"fields": {...}, "is_array": bool}`` for struct / array-of-struct fields

    Array detection: any observed list value establishes 0..* cardinality.
    """
    if not values:
        return "sc:Text"

    is_array = any(isinstance(v, list) for v in values)

    if is_array:
        inner = [
            item
            for v in values
            if isinstance(v, list)
            for item in v
            if item is not None
        ]
        if not inner:
            return "sc:Text"
        if sum(1 for v in inner if isinstance(v, dict)) > len(inner) / 2:
            return {
                "fields": infer_json_schema(inner, _top_level=False),
                "is_array": True,
            }
        votes: dict = {}
        for v in inner:
            t = infer_croissant_type(v)
            votes[t] = votes.get(t, 0) + 1
        return {
            "type": max(votes, key=votes.get) if votes else "sc:Text",
            "is_array": True,
        }

    if sum(1 for v in values if isinstance(v, dict)) > len(values) / 2:
        return {
            "fields": infer_json_schema(values, _top_level=False),
            "is_array": False,
        }

    votes = {}
    for v in values:
        t = infer_croissant_type(v)
        votes[t] = votes.get(t, 0) + 1
    return max(votes, key=votes.get) if votes else "sc:Text"


def infer_json_schema(records: list, _top_level: bool = True) -> dict:
    """Infer a column schema from a list of JSON/FHIR resource dicts.

    Uses majority-vote so minority null/unexpected values don't override the
    dominant type. The ``resourceType`` discriminator is excluded at the top
    level. Recursively expands dict and list-of-dict fields into sub-schemas.

    Args:
        records: JSON object dicts (top level) or nested sub-objects.
        _top_level: When True, skips the ``resourceType`` key (FHIR discriminator).

    Returns:
        Dict mapping field name → type string or ``{"fields": ..., "is_array": bool}``.
    """
    from collections import defaultdict as _defaultdict

    if not records:
        return {}
    field_values: dict = _defaultdict(list)
    for record in records:
        if not isinstance(record, dict):
            continue
        for key, val in record.items():
            if _top_level and key == "resourceType":
                continue
            if val is not None:
                field_values[key].append(val)
    return {
        key: infer_field_type(vals)
        for key, vals in sorted(field_values.items())
        if vals
    }


def build_fields_from_json_schema(
    col_schema: dict,
    parent_id: str,
    source_ref: dict,
    description_prefix: str = "Column",
    _col_path_prefix: str = "",
    used_field_ids: set = None,
) -> list:
    """Recursively build mlc.Field objects from a JSON column schema dict.

    Scalar primitive  → data_types=[type_string].
    Primitive array   → data_types=[type_string], is_array=True.
    Struct            → sub_fields (no data_types).
    Array-of-struct   → sub_fields + is_array=True.

    Args:
        col_schema: Schema dict as returned by ``infer_json_schema``.
        parent_id: Croissant @id of the parent RecordSet or Field.
        source_ref: Dict with either ``file_object=`` or ``file_set=`` key.
        description_prefix: Label prefix for field descriptions (default "Column").
        _col_path_prefix: Internal prefix for nested column paths; callers omit.
        used_field_ids: Optional set of already-emitted field @id values
            within the parent RecordSet. The function adds chosen ids to
            it so callers can detect collisions across multiple invocations
            against the same RecordSet. Defaults to a fresh per-call set.

    Returns:
        List of ``mlc.Field`` objects.
    """
    if used_field_ids is None:
        used_field_ids = set()
    fields = []
    for col_name, type_info in col_schema.items():
        field_id = make_field_id(parent_id, col_name, used_field_ids)
        col_path = f"{_col_path_prefix}/{col_name}" if _col_path_prefix else col_name
        source = mlc.Source(extract=mlc.Extract(column=col_path), **source_ref)

        if isinstance(type_info, dict) and "fields" in type_info:
            is_array = type_info.get("is_array", False)
            sub_fields = build_fields_from_json_schema(
                type_info["fields"],
                field_id,
                source_ref,
                description_prefix="Field",
                _col_path_prefix=col_path,
            )
            fields.append(
                mlc.Field(
                    id=field_id,
                    name=col_name,
                    description=f"{description_prefix} '{col_name}'",
                    is_array=True if is_array else None,
                    array_shape=ARRAY_SHAPE_UNKNOWN_1D if is_array else None,
                    source=source,
                    sub_fields=sub_fields or None,
                )
            )
        elif isinstance(type_info, dict) and "type" in type_info:
            fields.append(
                mlc.Field(
                    id=field_id,
                    name=col_name,
                    description=f"{description_prefix} '{col_name}'",
                    data_types=[type_info["type"]],
                    is_array=True,
                    array_shape=ARRAY_SHAPE_UNKNOWN_1D,
                    source=source,
                )
            )
        else:
            fields.append(
                mlc.Field(
                    id=field_id,
                    name=col_name,
                    description=f"{description_prefix} '{col_name}'",
                    data_types=[type_info],
                    source=source,
                )
            )
    return fields


def display_name(meta: dict) -> str:
    """What a description should call this file: its name as stored on disk.

    Identifiers come from the logical name, so ``sample.csv`` and
    ``sample.csv.gz`` describe one table; prose names the file the reader can
    find. The generator supplies ``stored_name``; ``file_name`` is the fallback
    for a handler invoked outside the pipeline.
    """
    return meta.get("stored_name") or meta.get("file_name", "unknown")


def get_clean_record_name(file_name: str) -> str:
    """
    Generate a clean record set name from a file name.

    Removes common file extensions in a generic way, not hardcoded to any format.

    Args:
        file_name: Original file name

    Returns:
        Clean name suitable for record set naming. Returns original name if
        cleaning would result in empty string.
    """
    if not file_name or not isinstance(file_name, str):
        logger.warning(f"Invalid file_name provided: {repr(file_name)}")
        return str(file_name) if file_name else "unknown"

    name = file_name.strip()

    # Remove common data file extensions
    extensions = [".csv", ".tsv", ".ndjson", ".json", ".parquet", ".txt", ".dat"]
    for ext in extensions:
        if name.endswith(ext):
            name = name[: -len(ext)]
            break

    # Ensure we return something valid
    return name if name else file_name
