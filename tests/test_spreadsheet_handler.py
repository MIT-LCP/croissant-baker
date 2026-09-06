"""What the shared contract sweep cannot express about workbooks."""

import datetime
from pathlib import Path

import openpyxl
import pytest

import croissant_baker.handlers.spreadsheet_handler as spreadsheet_handler_module
from croissant_baker.handlers.spreadsheet_handler import SpreadsheetHandler
from croissant_baker.sources import make_source
from tests.helpers import DATA, bake, record_sets

_LOGGER = "croissant_baker.handlers.spreadsheet_handler"
_LEGACY = DATA / "spreadsheets" / "manifest.xls"

#: The rows of the committed .xls fixture, to be written as its modern twin.
_MANIFEST = [
    ["sample_id", "tissue", "rin", "reads", "passed", "collected"],
    ["S1", "lung", 9.1, 400000000, True, datetime.date(2024, 3, 1)],
    ["S2", "liver", 8.4, 120000000, False, datetime.date(2024, 3, 2)],
    # A row with nothing on it but an error, which BIFF stores as a code. The
    # modern reader hands back the same text for a real error cell.
    [None, None, None, None, None, "#N/A"],
]

_SIDE_BY_SIDE = [
    ["metric", "value", None, "note", "author"],
    ["depth", 30, None, "QC passed", "jd"],
]

#: One sheet layout each, and the ``(columns, rows)`` it reads as — or None
#: where the sheet is not one table.
_LAYOUTS = {
    "preamble-above-the-header": (
        [
            ["Supplementary Table 1. Differentially expressed genes"],
            [],
            ["Contact", "j.doe@lab.example"],
            ["gene", "log2FC", "padj", "tissue"],
            ["TP53", -2.1, 0.001, "lung"],
        ],
        (["gene", "log2FC", "padj", "tissue"], 1),
    ),
    "cells-the-writer-left-empty": (
        [["gene", "padj", None], ["TP53", 0.001, None]],
        (["gene", "padj"], 1),
    ),
    "a-blank-row-ends-the-table": (
        [["gene", "padj"], ["TP53", 0.001], [], ["*p<0.05, two-sided"]],
        (["gene", "padj"], 1),
    ),
    "whitespace-counts-as-blank": (
        [[" gene ", "padj"], ["TP53", 0.001], ["   ", "  "], ["MYC", 0.02]],
        (["gene", "padj"], 1),
    ),
    "a-gap-under-the-header": (
        [["gene", "padj"], [], ["TP53", 0.001], ["MYC", 0.02]],
        (["gene", "padj"], 2),
    ),
    "more-rows-than-the-header-window": (
        [["gene", "padj"]] + [[f"G{n}", 0.01] for n in range(150)],
        (["gene", "padj"], 150),
    ),
    "two-tables-side-by-side": (_SIDE_BY_SIDE, None),
    "header-with-no-data": ([["gene", "log2FC"]], None),
    "empty": ([], None),
}

#: One column's values each, and the Croissant type they are read as. The
#: subject column rides with a second column so no row is wholly blank.
_TYPES = {
    "a-fraction-widens-the-column": ([1, 2, 2.5], "cr:Float64"),
    "the-odd-value-out-loses": ([1, 2, 3, "n/a"], "cr:Int64"),
    "blank-cells-are-not-values": ([1, None, None], "cr:Int64"),
    "nothing-at-all": ([None, None], "sc:Text"),
    "dates": (
        [datetime.datetime(2024, 3, 1), datetime.datetime(2024, 3, 2)],
        "sc:DateTime",
    ),
    "times": ([datetime.time(9, 30), datetime.time(10, 0)], "sc:Time"),
    "formulas-read-at-their-value": (["=1+1", "=2+2", 5], "cr:Int64"),
}


@pytest.fixture
def handler() -> SpreadsheetHandler:
    return SpreadsheetHandler()


def _book(path: Path, sheets: dict) -> Path:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    book.save(path)
    return path


def _sheets(handler: SpreadsheetHandler, path: Path) -> list:
    return handler.extract(make_source(path))["sheets"]


@pytest.mark.parametrize(("rows", "expected"), _LAYOUTS.values(), ids=_LAYOUTS)
def test_a_sheet_is_read_as_the_one_table_it_holds(
    handler: SpreadsheetHandler, tmp_path: Path, rows: list, expected
) -> None:
    """The header is the first row filling the sheet's used width, and that one
    rule is the whole of it: skip a title block, measure the width from values
    rather than from cells a writer left behind, count only rows that hold
    something, and give up rather than guess when no row fills the width.

    Each layout rides with a sheet that does read, so a refusal shows up as an
    absence instead of failing the whole workbook.
    """
    path = _book(tmp_path / "layout.xlsx", {"anchor": [["a"], [1]], "subject": rows})

    described = {sheet["name"]: sheet for sheet in _sheets(handler, path)}

    assert "anchor" in described, "the anchor sheet stopped reading"
    sheet = described.get("subject")
    if expected is None:
        assert sheet is None
    else:
        assert (sheet["columns"], sheet["num_rows"]) == expected


@pytest.mark.parametrize(("values", "expected"), _TYPES.values(), ids=_TYPES)
def test_a_column_is_typed_by_the_values_it_holds(
    handler: SpreadsheetHandler, tmp_path: Path, values: list, expected: str
) -> None:
    """A majority vote, except that a fraction anywhere widens a whole-number
    column — the promotion the CSV reader makes for the same case. Blanks are
    not values, and a formula counts as what it evaluated to, not as its text.
    """
    rows = [["subject", "anchor"]] + [[value, "x"] for value in values]
    path = _book(tmp_path / "types.xlsx", {"s": rows})

    (sheet,) = _sheets(handler, path)

    assert sheet["column_types"][0] == expected


def test_each_sheet_becomes_a_record_set_of_its_own_columns(dataset: Path) -> None:
    """Asserted on the baked document, because ``no extract`` is only visible
    there: mlc.Source fills in an empty Extract that never reaches the JSON."""
    _book(
        dataset / "study.xlsx",
        {
            "samples": _MANIFEST,
            "platforms": [["platform_id", "reads"], ["GPL24676", 400000000]],
        },
    )

    sets = {node["name"]: node for node in record_sets(bake(dataset))}

    assert {
        name: [f["name"] for f in node["field"]] for name, node in sets.items()
    } == {
        "samples": ["sample_id", "tissue", "rin", "reads", "passed", "collected"],
        "platforms": ["platform_id", "reads"],
    }
    # dataType round-trips as an rdflib URIRef, whose __eq__ refuses a str.
    assert [str(f["dataType"]) for f in sets["samples"]["field"]] == [
        "sc:Text",
        "sc:Text",
        "cr:Float64",
        "cr:Int64",
        "sc:Boolean",
        "sc:DateTime",
    ]
    assert all(
        field["source"] == {"fileObject": {"@id": "file_0"}}
        for node in sets.values()
        for field in node["field"]
    )


def test_only_sheets_holding_something_unreadable_are_warned_about(
    handler: SpreadsheetHandler, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One record per file, not per sheet, and the empty ones stay out of it:
    Excel leaves Sheet2 behind on every new workbook, so naming it would be
    noise, while a directory of workbooks warning per sheet would flood the
    terminal the coverage report bounds.
    """
    path = _book(
        tmp_path / "mixed.xlsx",
        {
            "good": [["a", "b"], [1, 2]],
            "side by side": _SIDE_BY_SIDE,
            "header only": [["gene", "log2FC"]],
            "Sheet2": [],
        },
    )

    with caplog.at_level("WARNING", logger=_LOGGER):
        sheets = _sheets(handler, path)

    assert [sheet["name"] for sheet in sheets] == ["good"]
    (record,) = [r for r in caplog.records if r.levelname == "WARNING"]
    assert "'side by side', 'header only'" in record.message
    assert "Sheet2" not in record.message
    assert "mixed.xlsx" in record.message


def test_a_workbook_holding_no_table_fails_by_name(
    handler: SpreadsheetHandler, tmp_path: Path
) -> None:
    """The other branch: a workbook that reads but says nothing is a failure
    with a reason, not an empty description."""
    path = _book(tmp_path / "layout.xlsx", {"grid": _SIDE_BY_SIDE})

    with pytest.raises(ValueError, match="layout.xlsx"):
        handler.extract(make_source(path))


def test_a_legacy_workbook_describes_like_its_modern_twin(
    handler: SpreadsheetHandler, tmp_path: Path
) -> None:
    """BIFF stores whole numbers and booleans as doubles and dates as serial
    offsets, so the two readers agree only because the .xls adapter converts
    all three back. The fixture carries one column of each."""
    modern = _book(tmp_path / "manifest.xlsx", {"manifest": _MANIFEST})

    legacy = _sheets(handler, _LEGACY)

    assert [(sheet["columns"], sheet["column_types"]) for sheet in legacy] == [
        (
            ["sample_id", "tissue", "rin", "reads", "passed", "collected"],
            [
                "sc:Text",
                "sc:Text",
                "cr:Float64",
                "cr:Int64",
                "sc:Boolean",
                "sc:DateTime",
            ],
        )
    ]
    assert legacy == _sheets(handler, modern)


def test_each_workbook_format_reports_its_own_media_type(
    handler: SpreadsheetHandler, tmp_path: Path
) -> None:
    """A macro-enabled workbook is an OOXML zip, so it reads like .xlsx and is
    typed unlike it. Nothing else in the suite carries one."""
    table = {"s": [["a"], [1]]}

    assert [
        handler.extract(make_source(path))["encoding_format"]
        for path in (
            _book(tmp_path / "book.xlsx", table),
            _book(tmp_path / "book.xlsm", table),
            _LEGACY,
        )
    ] == [
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroEnabled.12",
        "application/vnd.ms-excel",
    ]


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("~$study.xlsx", b"\x20\x00j\x00d\x00\x00\x00"),
        ("export.xls", b"sample_id\ttissue\nS1\tlung\n"),
    ],
    ids=["lock-file", "tsv-named-xls"],
)
def test_a_file_that_is_not_a_workbook_is_declined_at_debug(
    handler: SpreadsheetHandler,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    name: str,
    payload: bytes,
) -> None:
    """One case per signature. Lab systems export TSV and HTML under a
    spreadsheet name, and Excel's ~$ lock files begin with no dot, so discovery
    offers both here."""
    path = tmp_path / name
    path.write_bytes(payload)

    with caplog.at_level("DEBUG", logger=_LOGGER):
        assert handler.claims(make_source(path)) is False

    assert [
        r for r in caplog.records if r.levelname == "DEBUG" and name in r.message
    ], caplog.records


def test_both_readers_are_released_after_extraction(
    handler: SpreadsheetHandler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """read_only mode holds the archive open and xlrd holds the whole file, so
    a bake of a directory of workbooks must not accumulate either. Nothing but
    the release call distinguishes a closed reader from a collected one."""
    released = []

    def watch(module, opener: str, release: str) -> None:
        original = getattr(module, opener)

        def spy(*args, **kwargs):
            book = original(*args, **kwargs)
            close = getattr(book, release)

            def watched():
                released.append(book)
                return close()

            setattr(book, release, watched)
            return book

        monkeypatch.setattr(module, opener, spy)

    watch(spreadsheet_handler_module.openpyxl, "load_workbook", "close")
    watch(spreadsheet_handler_module.xlrd, "open_workbook", "release_resources")

    handler.extract(make_source(_book(tmp_path / "b.xlsx", {"s": [["a"], [1]]})))
    handler.extract(make_source(_LEGACY))

    assert len(released) == 2, f"a reader was left open: {released}"


def test_names_that_sanitize_alike_still_get_distinct_ids(
    handler: SpreadsheetHandler, tmp_path: Path
) -> None:
    """@id is unique document-wide, so a collision raises during assembly
    rather than quietly dropping a sheet or a column. Two columns of a sheet
    may share a header, and each keeps the type its own values earned."""
    path = _book(
        tmp_path / "plates.xlsx",
        {"A B": [["value", "value"], [1, "text"]], "A+B": [["y"], [2]]},
    )
    meta = handler.extract(make_source(path))
    meta["relative_path"] = "plates.xlsx"

    built = handler.build_croissant([meta], ["file_0"])

    assert [node.id for node in built.record_sets] == ["plates_A_B", "plates_A_B__1"]
    assert [
        (field.id, str(field.data_types[0])) for field in built.record_sets[0].fields
    ] == [
        ("plates_A_B/value", "cr:Int64"),
        ("plates_A_B/value__1", "sc:Text"),
    ]
