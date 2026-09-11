"""What a CIF or STAR document is, as ``cif.describe`` sees it.

Unit level: hand-written documents in, :class:`~...structural_biology.cif.Table`
descriptions out. The handler that turns those into record sets is covered in
``test_star_handler.py``.
"""

from __future__ import annotations

import pytest

from croissant_baker.handlers.structural_biology import cif

# The grammar under test is gemmi's, which ships in the optional
# structural-biology extra.
gemmi = pytest.importorskip("gemmi")

#: A two-block RELION particles file, trimmed to the columns that matter here:
#: one optics row, three particles, and a column of ``index@stack`` names that
#: must not be mistaken for numbers.
RELION = """
data_optics

loop_
_rlnOpticsGroupName
_rlnOpticsGroup
_rlnVoltage
opticsGroup1 1 300.0

data_particles

loop_
_rlnImageName
_rlnCoordinateX
_rlnClassNumber
000001@stack.mrcs 1104.5 1
000002@stack.mrcs 998.0 2
000003@stack.mrcs 512.25 1
"""


def describe(text: str) -> list:
    return cif.describe(gemmi.cif.read_string(text))


def by_name(tables: list) -> dict:
    return {table.name: table for table in tables}


def types(table) -> dict:
    return dict(table.columns)


def test_each_data_block_becomes_one_table() -> None:
    assert [table.name for table in describe(RELION)] == ["optics", "particles"]


def test_a_loop_reports_its_kind_and_row_count() -> None:
    particles = by_name(describe(RELION))["particles"]

    assert (particles.kind, particles.rows) == ("loop", 3)


def test_a_table_keeps_its_block_name() -> None:
    assert by_name(describe(RELION))["particles"].block == "particles"


def test_columns_keep_the_order_the_file_declares() -> None:
    particles = by_name(describe(RELION))["particles"]

    assert [name for name, _ in particles.columns] == [
        "rlnImageName",
        "rlnCoordinateX",
        "rlnClassNumber",
    ]


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        ("rlnImageName", "sc:Text"),
        ("rlnCoordinateX", "sc:Float"),
        ("rlnClassNumber", "sc:Integer"),
    ],
    ids=["stack reference", "coordinate", "class"],
)
def test_a_column_is_typed_from_its_values(column: str, expected: str) -> None:
    """``000001@stack.mrcs`` opens with digits and is not a number."""
    assert types(by_name(describe(RELION))["particles"])[column] == expected


def test_a_tag_loses_only_its_leading_underscore() -> None:
    """``_struct.title`` is one tag with a category prefix, not two names."""
    tables = describe("data_general\n_struct.title 'A structure'\n")

    assert [name for name, _ in tables[0].columns] == ["struct.title"]


def test_a_block_of_pairs_is_one_row() -> None:
    tables = describe(
        "data_general\n_rlnFinalResolution 3.2\n_rlnNrClasses 4\n_rlnJobName run1\n"
    )

    assert [(t.kind, t.rows) for t in tables] == [("pairs", 1)]
    assert types(tables[0]) == {
        "rlnFinalResolution": "sc:Float",
        "rlnNrClasses": "sc:Integer",
        "rlnJobName": "sc:Text",
    }


def test_a_quoted_value_is_typed_by_what_it_says() -> None:
    """The quotes are CIF syntax, not part of the value."""
    tables = describe("data_general\n_rlnFinalResolution '3.2'\n")

    assert types(tables[0]) == {"rlnFinalResolution": "sc:Float"}


def test_a_block_with_pairs_and_a_loop_yields_both() -> None:
    tables = describe(
        "data_job\n"
        "_rlnJobName run1\n"
        "loop_\n"
        "_rlnClassNumber\n"
        "_rlnClassDistribution\n"
        "1 0.5\n"
        "2 0.5\n"
    )

    assert [(t.kind, t.rows) for t in tables] == [("pairs", 1), ("loop", 2)]


def test_tables_of_one_block_are_named_apart() -> None:
    """Their names become record-set identifiers, so two cannot be one."""
    tables = describe(
        "data_job\n"
        "_rlnJobName run1\n"
        "loop_\n_rlnClassNumber\n1\n2\n"
        "loop_\n_rlnGroupName\ngroup1\n"
    )

    assert len({t.name for t in tables}) == 3
    assert all(t.name.startswith("job") for t in tables)


def test_a_lone_table_is_named_after_its_block_alone() -> None:
    """No index where there is nothing to tell apart."""
    assert [t.name for t in describe("data_optics\n_rlnVoltage 300\n")] == ["optics"]


def test_an_indexed_name_never_takes_a_real_blocks_name() -> None:
    """``data_job`` with two tables indexes them, and ``data_job_1`` is a block
    a file may also declare. Two tables cannot answer to one identifier."""
    tables = describe(
        "data_job\n"
        "_rlnJobName run1\n"
        "loop_\n_rlnClassNumber\n1\n2\n"
        "\ndata_job_1\n_rlnJobName run2\n"
    )

    assert len({t.name for t in tables}) == 3


def test_an_empty_block_describes_nothing() -> None:
    assert describe("data_optics\n_rlnVoltage 300\n\ndata_empty\n") == [
        table for table in describe("data_optics\n_rlnVoltage 300\n")
    ]


def test_a_document_with_no_block_describes_nothing() -> None:
    assert describe("") == []


def test_null_tokens_do_not_decide_a_type() -> None:
    """``.`` and ``?`` mean 'inapplicable' and 'unknown', not text."""
    tables = describe("data_t\nloop_\n_rlnCoordinateX\n1.5\n.\n?\n2.5\n")

    assert types(tables[0]) == {"rlnCoordinateX": "sc:Float"}


def test_a_column_of_nothing_but_nulls_is_text() -> None:
    """Nothing in it says otherwise, and text is the type that never lies."""
    tables = describe("data_t\nloop_\n_rlnCoordinateX\n.\n?\n")

    assert types(tables[0]) == {"rlnCoordinateX": "sc:Text"}


def test_one_text_value_among_numbers_makes_the_column_text() -> None:
    tables = describe("data_t\nloop_\n_rlnCoordinateX\n1.5\nNaN-ish\n")

    assert types(tables[0]) == {"rlnCoordinateX": "sc:Text"}


def test_a_type_is_read_from_a_bounded_head_of_the_column() -> None:
    """A million-row particle table would otherwise be read twice: once to
    describe it and once to type it. The head decides, and the documented
    consequence is that a text value below the bound is not seen.
    """
    rows = ["1"] * cif.MAX_SAMPLE_ROWS + ["text-at-the-end"]
    tables = describe("data_t\nloop_\n_rlnClassNumber\n" + "\n".join(rows) + "\n")

    assert tables[0].rows == cif.MAX_SAMPLE_ROWS + 1
    assert types(tables[0]) == {"rlnClassNumber": "sc:Integer"}


def test_a_text_value_inside_the_bound_is_seen() -> None:
    rows = ["1"] * (cif.MAX_SAMPLE_ROWS - 1) + ["text-at-the-end"]
    tables = describe("data_t\nloop_\n_rlnClassNumber\n" + "\n".join(rows) + "\n")

    assert types(tables[0]) == {"rlnClassNumber": "sc:Text"}
