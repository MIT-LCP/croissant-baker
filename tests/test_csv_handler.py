"""Tests for CSV handler."""

from pathlib import Path
import pytest
from croissant_baker.handlers.csv_handler import CSVHandler
from croissant_baker.sources import make_source


def test_csv_handler_extract_metadata(tmp_path: Path) -> None:
    """Test CSV metadata extraction (default: no row counting)."""
    csv_content = "id,name,age\n1,Alice,25\n2,Bob,30"
    csv_file = tmp_path / "test.csv"
    csv_file.write_text(csv_content)

    handler = CSVHandler()
    metadata = handler.extract(make_source(csv_file))

    assert metadata["encoding_format"] == "text/csv"
    assert metadata["file_name"] == "test.csv"
    assert metadata["num_rows"] is None
    assert metadata["num_columns"] == 3
    assert metadata["columns"] == ["id", "name", "age"]

    column_types = metadata["column_types"]
    assert column_types["id"] == "cr:Int64"
    assert column_types["name"] == "sc:Text"
    assert column_types["age"] == "cr:Int64"


def test_csv_handler_count_rows(tmp_path: Path) -> None:
    """Test CSV metadata extraction with explicit row counting."""
    csv_content = "id,name,age\n1,Alice,25\n2,Bob,30"
    csv_file = tmp_path / "test.csv"
    csv_file.write_text(csv_content)

    handler = CSVHandler()
    metadata = handler.extract(make_source(csv_file), count_rows=True)

    assert metadata["num_rows"] == 2


def test_csv_handler_empty_file(tmp_path: Path) -> None:
    """Test empty CSV file handling."""
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("")

    handler = CSVHandler()
    with pytest.raises(ValueError):
        handler.extract(make_source(empty_csv))


def test_csv_handler_data_types(tmp_path: Path) -> None:
    """Test data type inference."""
    csv_content = "bool_col,float_col,text_col\ntrue,3.14,hello\nfalse,2.71,world"
    csv_file = tmp_path / "types.csv"
    csv_file.write_text(csv_content)

    handler = CSVHandler()
    metadata = handler.extract(make_source(csv_file))

    column_types = metadata["column_types"]
    assert column_types["bool_col"] == "sc:Boolean"
    assert column_types["float_col"] == "cr:Float64"
    assert column_types["text_col"] == "sc:Text"


def test_csv_build_croissant_single_file() -> None:
    handler = CSVHandler()
    meta = {
        "file_name": "patients.csv",
        "relative_path": "patients.csv",
        "column_types": {"id": "sc:Text", "age": "cr:Int64", "dob": "sc:Date"},
        "num_rows": 100,
    }
    filesets, record_sets = handler.build_croissant([meta], ["file_0"])

    assert filesets == []
    assert len(record_sets) == 1
    assert record_sets[0].name == "patients"
    assert len(record_sets[0].fields) == 3


def test_csv_build_croissant_multiple_files() -> None:
    handler = CSVHandler()
    metas = [
        {
            "file_name": "a.csv",
            "relative_path": "a.csv",
            "column_types": {"x": "sc:Text"},
            "num_rows": None,
        },
        {
            "file_name": "b.csv",
            "relative_path": "b.csv",
            "column_types": {"y": "cr:Float64"},
            "num_rows": 50,
        },
    ]
    filesets, record_sets = handler.build_croissant(metas, ["file_0", "file_1"])

    assert filesets == []
    assert len(record_sets) == 2
    assert {rs.name for rs in record_sets} == {"a", "b"}


def test_parse_conflict_known_format() -> None:
    idx, inferred = CSVHandler._parse_conflict(
        "In CSV column #2: CSV conversion error to int64"
    )
    assert idx == 2
    assert inferred == "int64"


def test_parse_conflict_unknown_returns_none() -> None:
    """Unrecognized message returns (None, None); caller handles the probe."""
    idx, inferred = CSVHandler._parse_conflict("completely unrecognized error text")
    assert idx is None
    assert inferred is None


def test_parse_conflict_unknown_falls_back_to_all_string(tmp_path: Path) -> None:
    """Unknown conflict messages must fall back to all-string types."""
    from unittest.mock import patch
    import pyarrow as pa

    csv_file = tmp_path / "typed.csv"
    csv_file.write_text("a,b,c\n1,2,3\n")
    seen_overrides = []

    def fake_read(
        file_path, convert_options, count_rows=False, delimiter=",", skip_rows=0
    ):
        overrides = {k: str(v) for k, v in (convert_options.column_types or {}).items()}
        seen_overrides.append(overrides)
        if overrides == {"a": "string", "b": "string", "c": "string"}:
            return (
                {"a": "sc:Text", "b": "sc:Text", "c": "sc:Text"},
                ["a", "b", "c"],
                None,
            )
        raise pa.lib.ArrowInvalid("future pyarrow changed this message")

    with patch.object(CSVHandler, "_parse_conflict", return_value=(None, None)):
        with patch.object(CSVHandler, "_header", return_value=["a", "b", "c"]):
            with patch.object(CSVHandler, "_read_streaming", side_effect=fake_read):
                meta = CSVHandler().extract(make_source(csv_file))

    assert meta["column_types"] == {"a": "sc:Text", "b": "sc:Text", "c": "sc:Text"}
    assert seen_overrides == [{}, {"a": "string", "b": "string", "c": "string"}]


def test_no_fd_leak_on_schema_only_reads(tmp_path: Path) -> None:
    """50 sequential schema-only reads must not raise a resource error (#53).

    CSVStreamingReader supports __exit__ (verified PyArrow 19.0.1); wrapping
    open_csv in a context manager guarantees fd release on all runtimes.
    """
    content = "a,b,c\n1,2.0,hello\n"
    files = [tmp_path / f"f{i}.csv" for i in range(50)]
    for f in files:
        f.write_text(content)

    handler = CSVHandler()
    for f in files:
        handler.extract(make_source(f), count_rows=False)


_PROBE_SET = (
    "#probe_set_file_format=2.0\n"
    "#panel_name=Visium Human Transcriptome Probe Set v2.0\n"
    "#reference_genome=GRCh38\n"
    "gene_id,probe_seq,included\n"
    "ENSG00000000003,GGTGACACC,TRUE\n"
    "ENSG00000000005,TCTGCATCT,TRUE\n"
)

_HEADER_AND_ROWS = "id,note\n1,#1 ranked\n2,plain\n"


_HASH_HEADER = "#,name,age\n1,Ann,30\n2,Bob,40\n"
_CHROM_HEADER = "#chrom,start,end\nchr1,100,200\nchr2,300,400\n"
_MIXED_PREAMBLE_AND_HASH_HEADER = "#meta\n" + _HASH_HEADER


@pytest.mark.parametrize(
    "text, columns",
    [
        (_PROBE_SET, ["gene_id", "probe_seq", "included"]),
        (_HEADER_AND_ROWS, ["id", "note"]),
        ("#c\n" * 100 + _HEADER_AND_ROWS, ["id", "note"]),
        (_CHROM_HEADER, ["#chrom", "start", "end"]),
        (_MIXED_PREAMBLE_AND_HASH_HEADER, ["#", "name", "age"]),
    ],
    ids=[
        "10x-probe-set",
        "hash-inside-data",
        "at-the-preamble-bound",
        "hash-chrom-header",
        "comment-then-hash-header",
    ],
)
def test_a_leading_comment_run_is_skipped_and_nothing_else_is(
    tmp_path: Path, text: str, columns: list
) -> None:
    """PyArrow read a ``#`` preamble as a one-column table and then rejected
    the real header. Row counts are asserted too: skipping one row too many
    silently drops data instead of failing."""
    path = tmp_path / "probe.csv"
    path.write_text(text, encoding="utf-8")

    meta = CSVHandler().extract(make_source(path, Path("probe.csv")), count_rows=True)

    assert meta["columns"] == columns
    assert meta["num_columns"] == len(columns)
    assert meta["num_rows"] == 2


def test_a_preamble_past_the_bound_is_not_skipped(tmp_path: Path) -> None:
    """The scan is bounded, so a file that is comments all the way down cannot
    cost an unbounded read. Past the bound the handler stops claiming a
    preamble at all, and the read fails rather than silently mis-parsing."""
    path = tmp_path / "all_comments.csv"
    path.write_text("#c\n" * 101 + _HEADER_AND_ROWS, encoding="utf-8")

    assert CSVHandler()._preamble_rows(make_source(path, Path("all_comments.csv"))) == 0


def test_a_hash_header_is_not_a_preamble(tmp_path: Path) -> None:
    """A header that starts with ``#`` is a column name, not a comment.

    Skipping it makes the first data row the header: names go missing, types
    infer as Text, and a loader keyed on those names returns garbage.
    """
    path = tmp_path / "numbered.csv"
    path.write_text(_HASH_HEADER, encoding="utf-8")

    meta = CSVHandler().extract(
        make_source(path, Path("numbered.csv")), count_rows=True
    )

    assert meta["columns"] == ["#", "name", "age"]
    assert meta["column_types"]["#"] == "cr:Int64"
    assert meta["column_types"]["name"] == "sc:Text"
    assert meta["column_types"]["age"] == "cr:Int64"
    assert meta["num_rows"] == 2
