# Spreadsheets (synthetic)

Two workbooks, synthetic rather than borrowed: a real supplementary table
carries author and subject data, and nothing about this fixture needs it.
Between them they hold every sheet shape the handler distinguishes.

```
spreadsheets/
├── manifest.xls    one sheet, a table at A1 (OLE2/BIFF, ~5.5 KB)
└── study.xlsx      four sheets, one of each shape (OOXML, ~6.5 KB)
```

`manifest.xls` exists because **nothing in this project's dependency stack can
write the legacy format** — `xlrd` only reads, and `openpyxl` only handles
OOXML. It is the one committed byte-blob the spreadsheet handler needs, and it
is what `tests/helpers.py::_spreadsheet` hands the contract sweep and the
compression matrix. Its rows match the `.xlsx` twin written in
`tests/test_spreadsheet_handler.py`, which is how the two readers are held to
describing the same table the same way — so it carries one of each kind of cell
the adapter has to convert: a whole number and a boolean, which BIFF stores as
doubles, a date, which it stores as a serial offset, and — on a row holding
nothing else, so that dropping it would cost a whole row — an error.

`study.xlsx` is the end-to-end corpus. Its four sheets are the four outcomes:

| Sheet | Shape | Described as |
|---|---|---|
| `samples` | a title row, a blank row and a contact row above the header | a record set, preamble skipped |
| `platforms` | a table at A1 | a record set |
| `notes` | two tables side by side, with an empty column between | named in a warning |
| `Sheet3` | empty | skipped in silence |

## Regenerating

`xlwt` writes the legacy format and is otherwise dead, so it is fetched for the
one command rather than declared as a dependency:

```bash
uv run --with xlwt --with openpyxl --python 3.12 python - <<'PY'
import datetime
from pathlib import Path

import openpyxl
import xlwt

OUT = Path("tests/data/input/spreadsheets")
STAMP = datetime.datetime(2026, 9, 6, 12, 0, 0)

book = xlwt.Workbook()
sheet = book.add_sheet("manifest")
dates = xlwt.XFStyle()
dates.num_format_str = "YYYY-MM-DD"
rows = [
    ["sample_id", "tissue", "rin", "reads", "passed", "collected"],
    ["S1", "lung", 9.1, 400000000, True, datetime.date(2024, 3, 1)],
    ["S2", "liver", 8.4, 120000000, False, datetime.date(2024, 3, 2)],
]
for index, row in enumerate(rows):
    for column, value in enumerate(row):
        style = dates if isinstance(value, datetime.date) else xlwt.Style.default_style
        sheet.write(index, column, value, style)
# A row holding nothing but an error cell, typed as one rather than spelled as
# text: BIFF stores the code, and a reader that drops it loses the whole row.
sheet.row(3).set_cell_error(5, 0x2A)  # #N/A
book.save(str(OUT / "manifest.xls"))

book = openpyxl.Workbook()
book.remove(book.active)
for title, rows in {
    "samples": [
        ["Supplementary Table 1. Sequenced samples"],
        [],
        ["Contact", "j.doe@lab.example"],
        ["sample_id", "tissue", "rin", "collected"],
        ["S1", "lung", 9.1, datetime.date(2024, 3, 1)],
        ["S2", "liver", 8.4, datetime.date(2024, 3, 2)],
        ["S3", "kidney", 7.2, datetime.date(2024, 3, 5)],
    ],
    "platforms": [
        ["platform_id", "instrument", "reads"],
        ["GPL24676", "NovaSeq 6000", 400000000],
        ["GPL18573", "NextSeq 500", 120000000],
    ],
    "notes": [
        ["metric", "value", None, "note", "author"],
        ["depth", 30, None, "QC passed", "jd"],
    ],
    "Sheet3": [],
}.items():
    sheet = book.create_sheet(title)
    for row in rows:
        sheet.append(row)
book.properties.created = book.properties.modified = STAMP
book.save(OUT / "study.xlsx")
PY
```

`manifest.xls` comes back byte-identical. `study.xlsx` does not: openpyxl
stamps each ZIP entry with the current time, so a regenerated workbook holds the
same table in different bytes, and `contentSize` and `sha256` in
`tests/data/output/spreadsheets_croissant.jsonld` must be refreshed from the new
bake. Setting the document properties, as above, is not enough to stop it.
