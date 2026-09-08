"""Spreadsheet handler for Excel workbooks.

A workbook is a drawing surface, not a schema: nothing stops an author putting
three tables on one sheet, or notes beside them. This handler reads the one
layout that carries almost all real data — a single table per sheet — and names
any sheet it cannot read that way in a warning instead of guessing at it.

Rows above the table are skipped, because a title and a contact block above a
header is the ordinary shape of a supplementary file. The header is the first
row that fills the sheet's used width; everything below it is data.

Formula cells are read at their cached value, which is what Excel last wrote. A
workbook produced by a script and never opened in Excel carries no cache, so
those cells read as empty.

Fields carry no ``extract``: mlcroissant has no reader for either format, and
Croissant has no way to name a sheet, so a column reference would be a promise
nothing can keep.
"""

import itertools
import logging
from typing import Iterator

import mlcroissant as mlc
import openpyxl
import xlrd

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.utils import (
    SCHEMA_SAMPLE,
    display_name,
    infer_croissant_type,
    make_field_id,
    make_record_set_ids,
    sanitize_id,
)
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

_XLSX = ".xlsx"
_XLSM = ".xlsm"
_XLS = ".xls"

# OOXML is a ZIP; the legacy format is an OLE2 compound file. A lab system that
# exports HTML or TSV under a spreadsheet name is common enough that the name
# alone is not evidence, and Excel's ~$ lock files are not hidden from discovery.
_ZIP_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

_MAGIC = {_XLSX: _ZIP_MAGIC, _XLSM: _ZIP_MAGIC, _XLS: _OLE2_MAGIC}

_MEDIA_TYPES = {
    _XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    _XLSM: "application/vnd.ms-excel.sheet.macroEnabled.12",
    _XLS: "application/vnd.ms-excel",
}

#: How far down a sheet the header may sit. Bounded the way the CSV preamble is:
#: a table that starts below this is not one a reader should go hunting for.
_HEADER_SEARCH_ROWS = 100


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _width(row: tuple) -> int:
    """The 1-based index of the last cell in ``row`` that holds something."""
    for index in range(len(row), 0, -1):
        if not _blank(row[index - 1]):
            return index
    return 0


def _fills(row: tuple, width: int) -> bool:
    return len(row) >= width and not any(_blank(value) for value in row[:width])


def _column_type(values: list) -> str:
    """The Croissant type for one column, by majority of its sampled values.

    A column holding both whole numbers and fractions is one numeric column,
    which is the promotion the CSV reader makes for the same case.
    """
    if not values:
        return "sc:Text"
    votes: dict = {}
    for value in values:
        name = infer_croissant_type(value)
        votes[name] = votes.get(name, 0) + 1
    winner = max(votes, key=votes.get)
    if winner == "cr:Int64" and "cr:Float64" in votes:
        return "cr:Float64"
    return winner


def _read_sheet(rows: Iterator[tuple]) -> tuple:
    """``(whether the sheet held anything, the table it holds if it holds one)``.

    An empty sheet and a sheet that is not one table are different answers: the
    first has nothing to report, the second is what the warning is for.
    """
    window = list(itertools.islice(rows, _HEADER_SEARCH_ROWS))
    width = max((_width(row) for row in window), default=0)
    if not width:
        return False, None

    header_at = next(
        (index for index, row in enumerate(window) if _fills(row, width)), None
    )
    if header_at is None:
        return True, None

    columns = [str(value).strip() for value in window[header_at][:width]]
    samples: list = [[] for _ in columns]
    num_rows = 0
    for row in itertools.chain(window[header_at + 1 :], rows):
        if not _width(row):
            # The table is the first run of rows under its header, the way a
            # spreadsheet's own current region is bounded. Reading past a blank
            # row counts a footnote, or a second table, as data.
            if num_rows:
                break
            continue
        num_rows += 1
        if num_rows > SCHEMA_SAMPLE:
            continue
        for index in range(width):
            value = row[index] if index < len(row) else None
            if not _blank(value):
                samples[index].append(value)

    if not num_rows:
        return True, None

    # A list rather than the usual name -> type mapping: two columns of a sheet
    # may carry the same header, and a mapping would describe one of them with
    # the other's type while still emitting both.
    return True, {
        "columns": columns,
        "column_types": [_column_type(values) for values in samples],
        "num_rows": num_rows,
    }


def _xls_value(cell, datemode: int):
    """One legacy cell as the object the modern reader would have returned.

    BIFF stores every number as a double, every date as a serial offset and
    every error as a code, so without this the same table would type differently
    in the two formats — and a row holding nothing but an error would go from a
    row to a blank.
    """
    if cell.ctype == xlrd.XL_CELL_DATE:
        return xlrd.xldate.xldate_as_datetime(cell.value, datemode)
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(cell.value)
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        return int(cell.value) if float(cell.value).is_integer() else cell.value
    if cell.ctype == xlrd.XL_CELL_TEXT:
        return cell.value
    return None


def _read_with_xlrd(source: FileSource) -> list:
    """Read the legacy format, which has no streaming reader.

    The whole file is held in memory. A sheet cannot exceed 65,536 rows in this
    format, so the ceiling is the format's rather than this reader's.
    """
    with source.open() as stream:
        book = xlrd.open_workbook(file_contents=stream.read())
    try:
        return [
            (
                sheet.name,
                _read_sheet(
                    tuple(_xls_value(cell, book.datemode) for cell in sheet.row(index))
                    for index in range(sheet.nrows)
                ),
            )
            for sheet in book.sheets()
        ]
    finally:
        book.release_resources()


def _read_with_openpyxl(source: FileSource) -> list:
    """Read OOXML, streaming each sheet and closing the archive afterwards."""
    with source.open() as stream:
        book = openpyxl.load_workbook(stream, read_only=True, data_only=True)
        try:
            return [
                (sheet.title, _read_sheet(sheet.iter_rows(values_only=True)))
                for sheet in book.worksheets
            ]
        finally:
            book.close()


def _sheet_id(base_id: str, sheet_name: str, used: set) -> str:
    """A record set @id for one sheet, unique within its workbook.

    Underscore rather than slash: mlcroissant composes a field's uuid as
    ``{record set}/{field}``, so a slash here is one a reader has to guess at.
    """
    candidate = f"{base_id}_{sanitize_id(sheet_name)}"
    unique, attempt = candidate, 1
    while unique in used:
        unique = f"{candidate}__{attempt}"
        attempt += 1
    used.add(unique)
    return unique


class SpreadsheetHandler(FileTypeHandler):
    """Handler for Excel workbooks, one record set per readable sheet."""

    EXTENSIONS = (_XLS, _XLSM, _XLSX)
    FORMAT_NAME = "Spreadsheet"
    FORMAT_DESCRIPTION = "Per sheet: column names, inferred types, row count"

    def claims(self, source: FileSource) -> bool:
        magic = _MAGIC.get(source.suffix)
        if magic is None or not source.exists:
            return False
        if source.peek(len(magic)) == magic:
            return True
        # Debug, not warning: the generator names every undescribed file once
        # through a capped path, so warning here doubles it and escapes the cap.
        logger.debug(
            "Skipping %s: it does not open with the signature its name implies",
            source.relative_path,
        )
        return False

    def extract(self, source: FileSource, **kwargs) -> dict:
        """Describe every sheet of one workbook that holds a single table."""
        if not source.exists:
            raise FileNotFoundError(f"Spreadsheet not found: {source.relative_path}")

        reader = _read_with_xlrd if source.suffix == _XLS else _read_with_openpyxl
        try:
            sheets = reader(source)
        except Exception as exc:
            raise ValueError(
                f"Failed to read spreadsheet {source.relative_path}: {exc}"
            ) from exc

        described, unreadable = [], []
        for name, (had_content, table) in sheets:
            if table is not None:
                described.append({"name": name, **table})
            elif had_content:
                unreadable.append(name)

        if unreadable:
            logger.warning(
                "%s: could not read %s as a single table — every sheet should hold "
                "one standalone table, with a header row above its data",
                source.relative_path,
                ", ".join(repr(name) for name in unreadable),
            )
        if not described:
            raise ValueError(f"No sheet of {source.relative_path} holds a single table")

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": _MEDIA_TYPES[source.suffix],
            "sheets": described,
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """One RecordSet per sheet, all sourced from the workbook's FileObject."""
        record_sets: list = []
        for file_id, meta, base_id in zip(
            file_ids, file_metas, make_record_set_ids(file_metas)
        ):
            shown = display_name(meta)
            used_ids: set = set()
            for sheet in meta["sheets"]:
                record_sets.append(
                    self._record_set(sheet, file_id, base_id, shown, used_ids)
                )
        return BuildResult([], record_sets)

    @staticmethod
    def _record_set(
        sheet: dict, file_id: str, base_id: str, shown: str, used_ids: set
    ) -> mlc.RecordSet:
        record_set_id = _sheet_id(base_id, sheet["name"], used_ids)
        used_field_ids: set = set()
        fields = [
            mlc.Field(
                id=make_field_id(record_set_id, column, used_field_ids),
                name=column,
                description=f"Column '{column}' of sheet '{sheet['name']}' in {shown}",
                data_types=[column_type],
                source=mlc.Source(file_object=file_id),
            )
            for column, column_type in zip(sheet["columns"], sheet["column_types"])
        ]
        return mlc.RecordSet(
            id=record_set_id,
            name=sheet["name"],
            description=(
                f"Sheet '{sheet['name']}' of {shown} ({sheet['num_rows']} rows)"
            ),
            fields=fields,
        )
