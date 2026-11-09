"""CSV file handler for tabular data processing."""

import logging
import re

import mlcroissant as mlc
import pyarrow as pa
import pyarrow.csv as pa_csv

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.sources import FileSource
from croissant_baker.handlers.utils import (
    get_clean_record_name,
    infer_column_types_from_arrow_schema,
    display_name,
    make_field_id,
    make_record_set_ids,
)

logger = logging.getLogger(__name__)

# Pattern for extracting column index and inferred type from an ArrowInvalid
# exception message. PyArrow exposes no structured attributes on ArrowInvalid
# (verified PyArrow 19.0.1: only .args, .add_note, .with_traceback), so the
# message string is the only source of column information.
#
# DuckDB and Polars were evaluated as replacements: DuckDB reads from a path
# rather than a stream, and Polars' collect_schema() uses ~200 MB RSS on a 35 MB
# table against PyArrow's ~11 MB.
_ARROW_COL_RE = re.compile(r"In CSV column #(\d+): CSV conversion error to (\w+)")

# Max promotions before falling back to all-string types. One retry per conflicting
# column; beyond this we read everything as strings to bound I/O.
_MAX_TYPE_CONFLICT_RETRIES = 50


def _pandas_names(
    source: FileSource, header: list[str], delimiter: str, skip_rows: int
) -> list[str]:
    """Name the columns the way the loader will, if pyarrow's names are unclear.

    mlcroissant reads CSV and TSV through ``pandas.read_csv``, so a field must
    extract the name pandas gives its column; any other name validates but
    cannot be loaded. pyarrow keeps an empty cell (``DataFrame.to_csv()``
    writes one over its index) or a repeated name as is, while pandas renames
    them, so only then are the names read through pandas itself. pandas is
    not a direct dependency; it comes with mlcroissant, the reader matched.
    """
    if "" not in header and len(set(header)) == len(header):
        return header
    import pandas as pd

    with source.open() as stream:
        frame = pd.read_csv(stream, sep=delimiter, skiprows=skip_rows, nrows=0)
    names = [str(name) for name in frame.columns]
    return names if len(names) == len(header) else header


class CSVHandler(FileTypeHandler):
    """
    Handler for CSV files with automatic type inference.

    Supports:
    - Automatic column type detection using PyArrow
    - SHA256 hash computation for file integrity

    Uses PyArrow's streaming CSV reader (open_csv), given the decompressed
    stream the source provides, which:
    - Infers precise types (timestamp[s], date32, int64, float64, etc.)
    - Streams data for constant memory usage regardless of file size

    Type inference works in two stages:
    1. PyArrow infers column types from the first block of data.
    2. If a later block contains values incompatible with the inferred type
       (e.g. a float in an integer column), the affected column is promoted
       to a wider type and the file is re-read. Integer columns are first
       widened to float64; any remaining conflicts fall back to string.
       Only the conflicting column is overridden — all others keep their
       inferred types. If the conflicting column cannot be identified, the
       file falls back to all-string types to preserve correctness.

    Subclass this to support other delimiter-separated formats — override
    _suffix(), _delimiter() and _encoding_format(). See TSVHandler.
    """

    EXTENSIONS = (".csv",)
    FORMAT_NAME = "CSV"
    FORMAT_DESCRIPTION = "Column names, inferred types, optional row count"

    # Common timestamp formats for medical/clinical data beyond ISO-8601.
    # PyArrow uses ISO8601 by default; these cover additional patterns found
    # in datasets like MIMIC, eICU, and OMOP.
    _TIMESTAMP_PARSERS = [
        pa_csv.ISO8601,
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%m/%d/%Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ]

    # ------------------------------------------------------------------
    # Streaming with per-column type promotion
    # ------------------------------------------------------------------

    def _stream_csv(self, source: FileSource, count_rows: bool = False):
        """Return (column_types, columns, num_rows) by streaming the CSV."""
        overrides: dict = {}
        # Read only once a type conflict needs names to key overrides on.
        col_names: list | None = None
        # Handed to pyarrow when it would name two columns alike (two empty
        # cells, say), so each override lands on one column, not all of them.
        given_names: list | None = None
        header_lines = 1
        delimiter = self._delimiter()
        skip_rows = self._preamble_rows(source)

        for _ in range(_MAX_TYPE_CONFLICT_RETRIES):
            opts = pa_csv.ConvertOptions(
                timestamp_parsers=self._TIMESTAMP_PARSERS,
                column_types=overrides or None,
            )
            try:
                result = self._read_streaming(
                    source,
                    opts,
                    count_rows=count_rows,
                    delimiter=delimiter,
                    skip_rows=skip_rows,
                    column_names=given_names,
                    header_lines=header_lines,
                )
                if overrides:
                    logger.info(
                        "%s: promoted %d column(s) due to type conflicts",
                        source.relative_path,
                        len(overrides),
                    )
                return result
            except pa.lib.ArrowInvalid as exc:
                if col_names is None:
                    col_names, given_names, header_lines = self._lookup_names(
                        source, delimiter, skip_rows
                    )

                idx, inferred = self._parse_conflict(str(exc))

                if idx is not None and idx < len(col_names):
                    # Known conflict: promote the specific column.
                    name = col_names[idx]
                    # int/uint → float64 preserves numeric; other types → string.
                    if inferred.startswith(("int", "uint")) and name not in overrides:
                        overrides[name] = pa.float64()
                    else:
                        overrides[name] = pa.string()
                    logger.debug(
                        "%s: promoted column '%s' to %s",
                        source.relative_path,
                        name,
                        overrides[name],
                    )
                else:
                    break

        # Last resort: read everything as strings.
        if col_names is None:
            col_names, given_names, header_lines = self._lookup_names(
                source, delimiter, skip_rows
            )
        if len(overrides) >= _MAX_TYPE_CONFLICT_RETRIES:
            logger.warning(
                "%s: hit type conflict limit (%d), falling back to all-string types",
                source.relative_path,
                _MAX_TYPE_CONFLICT_RETRIES,
            )
        else:
            logger.warning(
                "%s: falling back to all-string types (could not parse type conflict)",
                source.relative_path,
            )
        opts = pa_csv.ConvertOptions(
            column_types={n: pa.string() for n in col_names},
        )
        return self._read_streaming(
            source,
            opts,
            count_rows=count_rows,
            delimiter=delimiter,
            skip_rows=skip_rows,
            column_names=given_names,
            header_lines=header_lines,
        )

    def _lookup_names(self, source: FileSource, delimiter: str, skip_rows: int):
        """Return ``(names, names to hand pyarrow or None, header lines)``.

        Handing pyarrow names replaces its header row, which ``skip_rows``
        then has to step over; it counts lines, and a quoted cell may hold a
        line break.
        """
        header = self._header(source, delimiter=delimiter, skip_rows=skip_rows)
        names = _pandas_names(source, header, delimiter, skip_rows)
        if names is header:
            return header, None, 1
        return names, names, 1 + sum(cell.count("\n") for cell in header)

    #: A leading ``#`` block is a metadata preamble, not data. 10x probe-set
    #: exports lead with one; PyArrow has no comment option, so the rows are
    #: counted and skipped instead. Bounded, because a file that is entirely
    #: comments has no header to find and is not a preamble at all.
    #: A ``#`` line is a comment only when it has fewer delimiters than the
    #: first nonempty non-``#`` row, so headers like ``#,name,age`` stay.
    _COMMENT_PREFIX = "#"
    _MAX_PREAMBLE_ROWS = 100

    @classmethod
    def _preamble_rows(cls, source: FileSource) -> int:
        """How many leading comment lines precede the header row.

        A ``#`` line is a comment only when it has fewer delimiters than the
        first nonempty non-``#`` row. Headers like ``#,name,age`` and
        ``#chrom,start,end`` match the row shape and are kept.
        """
        delim = cls._delimiter()
        hashes: list[int] = []
        in_preamble = True
        ref: int | None = None
        try:
            with source.open_text() as fh:
                for line in fh:
                    if in_preamble and line.startswith(cls._COMMENT_PREFIX):
                        hashes.append(line.count(delim))
                        if len(hashes) > cls._MAX_PREAMBLE_ROWS:
                            return 0
                        continue
                    in_preamble = False
                    if line.startswith(cls._COMMENT_PREFIX) or not line.strip():
                        continue
                    ref = line.count(delim)
                    break
        except (OSError, UnicodeDecodeError, EOFError):
            return 0
        if ref is None:
            return 0
        skip = 0
        for n in hashes:
            if n >= ref:
                break
            skip += 1
        return skip

    @staticmethod
    def _read_streaming(
        source: FileSource,
        convert_options,
        count_rows: bool = False,
        delimiter: str = ",",
        skip_rows: int = 0,
        column_names: list | None = None,
        header_lines: int = 1,
    ):
        """Open a streaming reader, extract schema, and optionally count rows.

        ``column_names``, when given, replace the header row, whose
        ``header_lines`` lines are skipped.

        Uses a context manager so the file descriptor is released immediately
        on exit — whether count_rows is True or False. Without it, CPython's
        reference-counting GC closes the fd on function return, but this is not
        guaranteed under PyPy or when an exception traceback holds a reference
        to the local frame. CSVStreamingReader implements __enter__/__exit__
        and has done so since PyArrow 3.x.
        """
        parse_options = pa_csv.ParseOptions(delimiter=delimiter)
        if column_names is None:
            read_options = pa_csv.ReadOptions(skip_rows=skip_rows)
        else:
            read_options = pa_csv.ReadOptions(
                skip_rows=skip_rows + header_lines, column_names=column_names
            )
        try:
            stream = source.open()
        except OSError as exc:
            raise ValueError(f"Cannot open {source.relative_path}: {exc}")

        try:
            with stream:
                reader_cm = pa_csv.open_csv(
                    stream,
                    read_options=read_options,
                    convert_options=convert_options,
                    parse_options=parse_options,
                )
                with reader_cm as reader:
                    schema = reader.schema
                    names = _pandas_names(source, schema.names, delimiter, skip_rows)
                    schema = pa.schema(f.with_name(n) for f, n in zip(schema, names))
                    column_types = infer_column_types_from_arrow_schema(schema)
                    columns = schema.names
                    num_rows = (
                        sum(batch.num_rows for batch in reader) if count_rows else None
                    )
        except UnicodeDecodeError as exc:
            raise ValueError(f"Encoding error in {source.relative_path}: {exc}")

        return column_types, columns, num_rows

    @staticmethod
    def _parse_conflict(msg: str):
        """Extract (column_index, inferred_type) from an ArrowInvalid message.

        Returns (None, None) when the message doesn't match the known format.
        The caller then falls back to all-string types for correctness.
        """
        m = _ARROW_COL_RE.search(msg)
        return (int(m.group(1)), m.group(2)) if m else (None, None)

    @staticmethod
    def _header(
        source: FileSource, delimiter: str = ",", skip_rows: int = 0
    ) -> list[str]:
        with (
            source.open() as stream,
            pa_csv.open_csv(
                stream,
                read_options=pa_csv.ReadOptions(skip_rows=skip_rows),
                parse_options=pa_csv.ParseOptions(delimiter=delimiter),
            ) as reader,
        ):
            return reader.schema.names

    @staticmethod
    def _delimiter() -> str:
        """Return the field delimiter for this format. Override in subclasses."""
        return ","

    @staticmethod
    def _suffix() -> str:
        """Return the logical suffix this handler claims. Override in subclasses."""
        return ".csv"

    @staticmethod
    def _encoding_format() -> str:
        """Return this format's own media type. Override in subclasses.

        The compression media type, if any, is added by the generator.
        """
        return "text/csv"

    # ------------------------------------------------------------------
    # FileTypeHandler interface
    # ------------------------------------------------------------------

    def claims(self, source: FileSource) -> bool:
        """Claim any CSV, wrapped or not — ``source.suffix`` is logical."""
        return source.suffix == self._suffix()

    def extract(self, source: FileSource, count_rows: bool = False, **kwargs) -> dict:
        """
        Extract comprehensive metadata from a CSV file.

        Uses PyArrow to read the CSV with automatic type inference,
        including timestamp detection and precise numeric types.

        Args:
            source: The CSV file, compression already resolved
            count_rows: If True, scan entire file for exact row count.
                        Defaults to False for performance (returns num_rows=None).

        Returns:
            Dictionary containing:
            - Basic file info (path, name, size, hash)
            - Format information (encoding)
            - Data structure (columns, types, row count)

        Raises:
            ValueError: If the CSV file cannot be read or processed
            FileNotFoundError: If the file doesn't exist
        """
        if not source.exists:
            raise FileNotFoundError(
                f"{self.FORMAT_NAME} file not found: {source.relative_path}"
            )

        try:
            column_types, columns, num_rows = self._stream_csv(
                source, count_rows=count_rows
            )
        except pa.lib.ArrowInvalid as exc:
            # ArrowInvalid is a ValueError subclass, so without this the scan
            # report would carry a raw parser message naming no file.
            raise ValueError(
                f"Failed to read {self.FORMAT_NAME} file {source.relative_path}: {exc}"
            ) from exc

        if count_rows and num_rows == 0:
            raise ValueError(f"CSV file is empty: {source.relative_path}")

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": self._encoding_format(),
            "column_types": column_types,
            "num_rows": num_rows,
            "num_columns": len(columns),
            "columns": columns,
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        record_sets = []
        rs_ids = make_record_set_ids(file_metas)
        for file_id, file_meta, rs_id in zip(file_ids, file_metas, rs_ids):
            rs_name = get_clean_record_name(file_meta["file_name"])
            shown = display_name(file_meta)

            used_field_ids: set = set()
            fields = []
            for col_name, col_type in file_meta["column_types"].items():
                field_id = make_field_id(rs_id, col_name, used_field_ids)
                field = mlc.Field(
                    id=field_id,
                    name=col_name,
                    description=f"Column '{col_name}' from {shown}",
                    data_types=[col_type],
                    source=mlc.Source(
                        file_object=file_id,
                        extract=mlc.Extract(column=col_name),
                    ),
                )
                fields.append(field)

            num_rows = file_meta.get("num_rows")
            row_desc = f" ({num_rows} rows)" if num_rows is not None else ""
            record_sets.append(
                mlc.RecordSet(
                    id=rs_id,
                    name=rs_name,
                    description=f"Records from {shown}{row_desc}",
                    fields=fields,
                )
            )

        return BuildResult([], record_sets)
