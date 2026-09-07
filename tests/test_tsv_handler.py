"""Tests for TSVHandler."""

from pathlib import Path


from croissant_baker.handlers.tsv_handler import TSVHandler
from croissant_baker.sources import make_source


def test_tsv_extract_metadata_types(tmp_path: Path) -> None:
    """Tab-separated data is parsed with correct types and MIME type."""
    tsv_file = tmp_path / "results.tsv"
    tsv_file.write_text("id\tname\tscore\n1\tAlice\t9.5\n2\tBob\t8.0\n")

    meta = TSVHandler().extract(make_source(tsv_file))

    assert meta["encoding_format"] == "text/tab-separated-values"
    assert meta["num_columns"] == 3
    assert meta["columns"] == ["id", "name", "score"]
    assert meta["column_types"]["id"] == "cr:Int64"
    assert meta["column_types"]["name"] == "sc:Text"
    assert meta["column_types"]["score"] == "cr:Float64"


def test_tsv_extract_metadata_count_rows(tmp_path: Path) -> None:
    tsv_file = tmp_path / "pairs.tsv"
    tsv_file.write_text("x\ty\n1\t2\n3\t4\n5\t6\n")

    meta = TSVHandler().extract(make_source(tsv_file), count_rows=True)

    assert meta["num_rows"] == 3


def test_tsv_extract_metadata_missing_values(tmp_path: Path) -> None:
    """Empty cells must not crash type inference."""
    tsv_file = tmp_path / "genes.tsv"
    tsv_file.write_text(
        "gene\texpression\tpvalue\nTP53\t12.4\t0.001\nBRCA1\t\t0.05\nEGFR\t3.1\t\n"
    )

    meta = TSVHandler().extract(make_source(tsv_file))

    assert meta["num_columns"] == 3
    assert "gene" in meta["column_types"]


def test_tsv_delimiter_not_confused_with_csv(tmp_path: Path) -> None:
    """A comma-separated file saved as .tsv is parsed with tab delimiter.

    The entire comma-separated line becomes one column because no tabs exist.
    This verifies TSVHandler forces the tab delimiter unconditionally.
    """
    tsv_file = tmp_path / "wrong.tsv"
    tsv_file.write_text("a,b,c\n1,2,3\n")

    meta = TSVHandler().extract(make_source(tsv_file))

    assert meta["num_columns"] == 1


def test_tsv_build_croissant() -> None:
    handler = TSVHandler()
    meta = {
        "file_name": "proteins.tsv",
        "relative_path": "proteins.tsv",
        "column_types": {"entry": "sc:Text", "length": "cr:Int64"},
        "num_rows": 200,
    }
    filesets, record_sets = handler.build_croissant([meta], ["file_0"])

    assert filesets == []
    assert len(record_sets) == 1
    assert record_sets[0].name == "proteins"
    assert len(record_sets[0].fields) == 2
