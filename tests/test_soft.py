"""The GEO SOFT line grammar, tested without Croissant in the way."""

from __future__ import annotations

import pytest

from croissant_baker.handlers import soft


def parse(text: str, **kwargs) -> soft.SoftFile:
    """Parse ``text`` the way the handler does: as raw byte lines."""
    return soft.parse(text.encode("utf-8").splitlines(keepends=True), **kwargs)


MINIMAL = """\
^DATABASE = GeoMiame
!Database_name = Gene Expression Omnibus (GEO)
^SERIES = GSE1
!Series_title = A series
^PLATFORM = GPL1
!Platform_title = A platform
^SAMPLE = GSM1
!Sample_title = A sample
"""


def test_each_entity_kind_collects_its_own_attribute_names() -> None:
    """The prefix comes off every kind, so a reader comparing the three record
    sets does not have to know that one of them kept it."""
    parsed = parse(MINIMAL)

    assert list(parsed.kinds) == ["DATABASE", "SERIES", "PLATFORM", "SAMPLE"]
    assert list(parsed.kinds["SERIES"].names) == ["title"]
    assert list(parsed.kinds["PLATFORM"].names) == ["title"]
    assert all(group.entities == 1 for group in parsed.kinds.values())


def test_an_attribute_whose_prefix_is_not_the_open_entitys_keeps_its_name() -> None:
    """Renaming it would report a name the deposit never wrote."""
    parsed = parse("^PLATFORM = GPL1\n!Sample_title = misplaced\n")

    assert list(parsed.kinds["PLATFORM"].names) == ["Sample_title"]


def test_repetition_is_counted_within_an_entity_and_not_across_them() -> None:
    """Two ``!Sample_description`` lines on one sample are one field with two
    values; one per sample across ten samples is one value each."""
    within = parse(
        "^SAMPLE = GSM1\n!Sample_description = one\n!Sample_description = two\n"
    )
    across = parse(
        "^SAMPLE = GSM1\n!Sample_title = a\n^SAMPLE = GSM2\n!Sample_title = b\n"
    )

    assert within.kinds["SAMPLE"].names == {"description": True}
    assert across.kinds["SAMPLE"].names == {"title": False}
    assert across.kinds["SAMPLE"].entities == 2


def test_an_attribute_with_no_value_is_still_an_attribute() -> None:
    """``!Platform_manufacture_protocol = `` appears in a real GPL96 export,
    and both spellings — with and without the trailing space — parse."""
    parsed = parse("^PLATFORM = GPL1\n!Platform_a = \n!Platform_b =\n")

    assert list(parsed.kinds["PLATFORM"].names) == ["a", "b"]


def test_the_row_count_is_read_and_still_named_as_an_attribute() -> None:
    """The parser reading a value does not stop the name being a name."""
    parsed = parse("^SAMPLE = GSM1\n!Sample_data_row_count = 22283\n")

    assert "data_row_count" in parsed.kinds["SAMPLE"].names


def test_an_attribute_before_any_entity_is_dropped() -> None:
    parsed = parse("!Series_title = orphan\n^SERIES = GSE1\n!Series_name = kept\n")

    assert list(parsed.kinds["SERIES"].names) == ["name"]


# ---------------------------------------------------------------------------
# Characteristics
# ---------------------------------------------------------------------------

CHARACTERISTICS = """\
^SAMPLE = GSM1
!Sample_characteristics_ch1 = tissue: liver
!Sample_characteristics_ch1 = dbgap_subject_id: 27278
^SAMPLE = GSM2
!Sample_characteristics_ch1 = tissue: kidney
"""


def test_characteristics_are_split_on_the_first_colon_space_and_stand_alone() -> None:
    """A one-channel deposit keeps the bare key, and the raw attribute name
    stays out of the sample attributes: listing it beside them would assert a
    field the characteristics record set already enumerates."""
    parsed = parse(CHARACTERISTICS)

    assert list(parsed.characteristics.names) == ["tissue", "dbgap_subject_id"]
    assert parsed.characteristics.entities == 2
    assert "characteristics_ch1" not in parsed.kinds["SAMPLE"].names


def test_a_key_repeated_within_one_sample_is_marked_repeated() -> None:
    parsed = parse(
        "^SAMPLE = GSM1\n"
        "!Sample_characteristics_ch1 = treatment: a\n"
        "!Sample_characteristics_ch1 = treatment: b\n"
    )

    assert parsed.characteristics.names == {"treatment": True}


def test_two_channels_prefix_every_key_not_only_the_colliding_one() -> None:
    """Which of two ``gender`` keys keeps the bare name must not depend on
    which the parser met first."""
    parsed = parse(
        "^SAMPLE = GSM1\n"
        "!Sample_characteristics_ch1 = gender: female\n"
        "!Sample_characteristics_ch2 = gender: male\n"
        "!Sample_characteristics_ch1 = age: 66\n"
    )

    assert list(parsed.characteristics.names) == ["ch1_gender", "ch2_gender", "ch1_age"]


def test_the_separator_is_the_literal_colon_space() -> None:
    """Not a bare colon. ``10:30`` inside a value must not become a key, a
    namespaced key must survive its own colons, and a line with no ``: `` at
    all does not follow the convention — which is a convention, not a grammar,
    so those lines are counted and collected under the attribute they arrived
    on rather than guessed at.
    """
    parsed = parse(
        "^SAMPLE = GSM1\n"
        "!Sample_characteristics_ch1 = collected: 10:30\n"
        "!Sample_characteristics_ch1 = efo:cell type: fibroblast\n"
        "!Sample_characteristics_ch1 = stage:\n"
        "!Sample_characteristics_ch1 = nokey:value\n"
    )

    assert list(parsed.characteristics.names) == [
        "collected",
        "efo:cell type",
        "characteristics_ch1",
    ]
    assert list(parsed.fallbacks) == ["characteristics_ch1"]
    assert parsed.unparsed == 2


def test_a_characteristic_with_an_empty_value_still_yields_its_key() -> None:
    """``diagnosis: `` names ``diagnosis``. Stripping the value before the
    split throws the key away, and a blank characteristic is common where a
    submitter left one column of a spreadsheet empty."""
    parsed = parse("^SAMPLE = GSM1\n!Sample_characteristics_ch1 = diagnosis: \n")

    assert list(parsed.characteristics.names) == ["diagnosis"]
    assert parsed.unparsed == 0


def test_a_repeated_malformed_line_is_marked_repeated_like_any_other() -> None:
    parsed = parse(
        "^SAMPLE = GSM1\n"
        "!Sample_characteristics_ch1 = free text\n"
        "!Sample_characteristics_ch1 = more free text\n"
    )

    assert parsed.characteristics.names == {"characteristics_ch1": True}
    assert parsed.unparsed == 2


def test_a_malformed_line_still_establishes_its_channel() -> None:
    """The channel is in the attribute name, so it is known whatever the value
    turned out to be — and if it did not count, valid channel-1 keys would
    keep the bare name in a file that plainly carries two channels."""
    parsed = parse(
        "^SAMPLE = GSM1\n"
        "!Sample_characteristics_ch1 = gender: female\n"
        "!Sample_characteristics_ch2 = free text\n"
    )

    assert "ch1_gender" in parsed.characteristics.names


def test_characteristics_outside_a_sample_stay_plain_attributes() -> None:
    parsed = parse("^SERIES = GSE1\n!Series_characteristics_ch1 = tissue: liver\n")

    assert parsed.characteristics.names == {}
    assert "characteristics_ch1" in parsed.kinds["SERIES"].names


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

TWO_SIGNATURES = """\
^PLATFORM = GPL1
!Platform_data_row_count = 2
#ID = Probe set identifier
!platform_table_begin
ID\tGB_ACC
1_at\tU48705
2_at\tM87338
!platform_table_end
^SAMPLE = GSM1
!Sample_data_row_count = 3
#VALUE = Intensity
!sample_table_begin
ID_REF\tVALUE\tSIG_LOG2
1_at\t320.5\t8.32
!sample_table_end
^SAMPLE = GSM2
!Sample_data_row_count = 5
!sample_table_begin
ID_REF\tVALUE\tSIGNAL_Log2
1_at\t305.4\t8.25
!sample_table_end
^SAMPLE = GSM3
!Sample_data_row_count = 7
!sample_table_begin
ID_REF\tVALUE\tSIGNAL_Log2
1_at\t299.1\t8.22
!sample_table_end
"""


def test_the_shape_a_table_carrying_export_declares() -> None:
    """Tables group on their exact column signature; row counts are summed from
    the declarations, so nothing has to be counted; and each column keeps the
    deposit's own ``#COLUMN`` line verbatim, because a reader has to be able to
    see that the prose came from the deposit and not from us.

    The attribute names are asserted exactly, which is what pins the ordering
    the grammar turns on: a marker line carries no ``=`` while every attribute
    line does, so a parser that split first would report ``sample_table_begin``
    as an attribute here and then read the body as metadata.
    """
    parsed = parse(TWO_SIGNATURES)

    assert [
        (t.kind, t.columns, t.entities, t.rows, t.rows_known) for t in parsed.tables
    ] == [
        ("PLATFORM", ("ID", "GB_ACC"), 1, 2, True),
        ("SAMPLE", ("ID_REF", "VALUE", "SIG_LOG2"), 1, 3, True),
        ("SAMPLE", ("ID_REF", "VALUE", "SIGNAL_Log2"), 2, 12, True),
    ]
    platform, first_sample, _ = parsed.tables
    assert platform.column_lines == {"ID": "#ID = Probe set identifier"}
    assert first_sample.column_lines == {"VALUE": "#VALUE = Intensity"}
    assert list(parsed.kinds["SAMPLE"].names) == ["data_row_count"]


def test_a_table_whose_entity_declared_no_count_reports_that() -> None:
    parsed = parse(
        "^SAMPLE = GSM1\n!sample_table_begin\nA\tB\n1\t2\n!sample_table_end\n"
    )

    assert parsed.tables[0].rows_known is False


def test_an_empty_column_description_is_dropped() -> None:
    """``#ID_REF =  `` appears ten times in one real export."""
    parsed = parse(
        "^SAMPLE = GSM1\n#ID_REF =  \n#VALUE = Intensity\n"
        "!sample_table_begin\nID_REF\tVALUE\n1_at\t3\n!sample_table_end\n"
    )

    assert parsed.tables[0].column_lines == {"VALUE": "#VALUE = Intensity"}


def test_an_empty_leading_table_does_not_exhaust_its_signatures_sample() -> None:
    """The bound is per signature, not per entity: a deposit may declare an
    empty table before a populated one, and stopping after the first entity
    types every column of the signature as text for ever."""
    parsed = parse(
        "^SAMPLE = GSM1\n!sample_table_begin\nID_REF\tVALUE\n!sample_table_end\n"
        "^SAMPLE = GSM2\n!sample_table_begin\nID_REF\tVALUE\n1_at\t3.5\n"
        "!sample_table_end\n"
    )

    (table,) = parsed.tables
    assert table.entities == 2
    assert table.sample == b"ID_REF\tVALUE\n1_at\t3.5\n"


def test_the_row_bound_is_not_spent_twice_on_one_signature() -> None:
    """Two entities of one signature share the bound rather than each getting
    it, so a forty-sample series still costs one sample's worth."""
    body = "".join(f"row{i}\t{i}\n" for i in range(50))
    parsed = parse(
        f"^SAMPLE = GSM1\n!sample_table_begin\nID_REF\tVALUE\n{body}"
        f"!sample_table_end\n"
        f"^SAMPLE = GSM2\n!sample_table_begin\nID_REF\tVALUE\n{body}"
        f"!sample_table_end\n",
        sample_rows=10,
    )

    (table,) = parsed.tables
    assert table.entities == 2
    assert table.sample.count(b"\n") == 11  # header plus ten rows in total


def test_one_oversized_row_does_not_end_the_sampling() -> None:
    """A row that will not fit is skipped, not fatal. Ending the sample there
    can leave a signature with nothing but its header, and every numeric
    column then types as text."""
    parsed = parse(
        "^SAMPLE = GSM1\n!sample_table_begin\nID_REF\tVALUE\n"
        f"huge\t{'x' * 3000}\n"
        "1_at\t3.5\n"
        "2_at\t4.5\n"
        "!sample_table_end\n",
        sample_bytes=2000,
    )

    (table,) = parsed.tables
    assert table.sample == b"ID_REF\tVALUE\n1_at\t3.5\n2_at\t4.5\n"


def test_the_row_sample_stops_at_the_byte_bound() -> None:
    """A row is not a bounded thing: GPL96 runs 2 KiB a row, and wider
    platforms exist."""
    body = "".join(f"row{i}\t{'x' * 500}\n" for i in range(50))
    parsed = parse(
        f"^SAMPLE = GSM1\n!sample_table_begin\nID_REF\tVALUE\n{body}!sample_table_end\n",
        sample_bytes=2000,
    )

    assert len(parsed.tables[0].sample) <= 2000
    assert parsed.tables[0].sample.count(b"\n") < 50


def test_an_attribute_whose_value_ends_in_a_marker_word_is_an_attribute() -> None:
    """A marker line carries no ``=`` at any position while every attribute
    line does. Matching on the suffix alone opens a table on ordinary content
    and reads the rest of the deposit as table body."""
    parsed = parse(
        "^SAMPLE = GSM1\n"
        "!Sample_title = literal_sample_table_begin\n"
        "!Sample_geo_accession = GSM1\n"
    )

    assert list(parsed.kinds["SAMPLE"].names) == ["title", "geo_accession"]
    assert parsed.tables == []
    assert parsed.incomplete == ()


def test_spaces_around_a_marker_are_forgiven_and_tabs_are_not() -> None:
    """A tab is data: ``!sample_table_end\\t`` is a two-column row whose second
    cell is empty, and a row carrying tabs cannot be a marker however it ends.
    Stripping the delimiter before matching closes the table on the first of
    them and throws away every row that follows, including the entity after it.
    """
    parsed = parse(
        "^SAMPLE = GSM1\n!sample_table_begin \nID_REF\tVALUE\n"
        "!sample_table_end\t\n"
        "!probe\tsample_table_end\n"
        "1_at\t3\n"
        "!sample_table_end \n"
        "^SERIES = GSE1\n!Series_title = after the table\n"
    )

    assert parsed.tables[0].columns == ("ID_REF", "VALUE")
    assert parsed.tables[0].sample.count(b"\n") == 4  # header and all three rows
    assert list(parsed.kinds["SERIES"].names) == ["title"]
    assert parsed.incomplete == ()


def test_a_table_body_line_is_never_read_as_a_line_of_metadata() -> None:
    parsed = parse(
        "^SAMPLE = GSM1\n!sample_table_begin\nID_REF\tVALUE\n"
        "^SERIES = not an entity\n!Sample_fake = not an attribute\n"
        "!sample_table_end\n"
    )

    assert list(parsed.kinds) == ["SAMPLE"]
    assert parsed.kinds["SAMPLE"].names == {}


def test_carriage_returns_reach_no_name() -> None:
    """A CRLF export must not name a column ``VALUE\\r``. The attribute names
    survive either way — the ``\\r`` falls after the ``=`` — so the table
    header is where dropping the line-ending handling actually bites.
    """
    parsed = soft.parse(
        [
            b"^SAMPLE = GSM1\r\n",
            b"!Sample_title = A sample\r\n",
            b"!sample_table_begin\r\n",
            b"ID_REF\tVALUE\r\n",
            b"1_at\t3\r\n",
            b"!sample_table_end\r\n",
        ]
    )

    assert parsed.tables[0].columns == ("ID_REF", "VALUE")
    assert list(parsed.kinds["SAMPLE"].names) == ["title"]


# ---------------------------------------------------------------------------
# Reading to the end, or saying that it did not
# ---------------------------------------------------------------------------

_COMPLETE = [b"^SAMPLE = GSM1\n", b"!Sample_title = a\n"]
_BAD_BYTE = b"!Sample_description = 37\xbaC\n"  # latin-1, not UTF-8
_OPEN_TABLE = [b"!sample_table_begin\n", b"ID_REF\tVALUE\n"]


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (_COMPLETE, ()),
        ([*_COMPLETE, *_OPEN_TABLE], (soft.TABLE_UNCLOSED,)),
        ([*_COMPLETE, _BAD_BYTE], (soft.UNDECODABLE.format(count=1),)),
        (
            [*_COMPLETE, _BAD_BYTE, *_OPEN_TABLE],
            (soft.TABLE_UNCLOSED, soft.UNDECODABLE.format(count=1)),
        ),
    ],
    ids=["complete", "table left open", "undecodable byte", "both"],
)
def test_what_a_partial_parse_reports(lines: list, expected: tuple) -> None:
    """An attribute block has no closing marker, so a file simply ending is not
    partial and must not be reported as one. What *was* read is still described
    either way, which the handler tests assert on the record sets."""
    assert soft.parse(lines).incomplete == expected


def test_a_file_with_no_entity_line_yields_no_kinds() -> None:
    """How a caller tells "not SOFT" from "SOFT that says little". The parser
    does not raise: it does not know what file it is reading."""
    parsed = soft.parse([b"\x00\xff not a real file \xfe\x00\n"])

    assert parsed.kinds == {}
    assert parsed.tables == []
