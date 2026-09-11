"""Tests for handler utilities."""

import io

from croissant_baker.handlers.utils import (
    ARRAY_SHAPE_UNKNOWN_1D,
    PrefixLines,
    _disambiguate_ids,
    allocate_record_set_ids,
    decode_line,
    deposited,
    determined,
    make_field_id,
    normalize_array_shape,
    plural,
    shard_template,
)


def metas(*paths: str) -> list:
    return [{"file_name": p.rsplit("/", 1)[-1], "relative_path": p} for p in paths]


def test_disambiguate_ids_no_collisions_keeps_bare_stems() -> None:
    """Stems unique within the batch pass through unchanged."""
    items = [("admissions", ["hosp"]), ("patients", ["hosp"]), ("icustays", ["icu"])]
    assert _disambiguate_ids(items) == ["admissions", "patients", "icustays"]


def test_disambiguate_ids_two_colliders_get_immediate_parent_prefix() -> None:
    """Two files with the same basename in distinct subdirectories are
    disambiguated by their immediate parent directory."""
    items = [("data", ["topic_a"]), ("data", ["topic_b"])]
    assert _disambiguate_ids(items) == ["topic_a__data", "topic_b__data"]


def test_disambiguate_ids_three_colliders_share_minimum_depth() -> None:
    """All members of a colliding group walk up to the same depth — the
    minimum at which every member is unique."""
    items = [
        ("data", ["root", "topic_a"]),
        ("data", ["root", "topic_b"]),
        ("data", ["root", "topic_c"]),
    ]
    # depth=1 (immediate parent) is sufficient: topic_a, topic_b, topic_c all differ.
    assert _disambiguate_ids(items) == [
        "topic_a__data",
        "topic_b__data",
        "topic_c__data",
    ]


def test_disambiguate_ids_walks_deeper_when_immediate_parents_collide() -> None:
    """When the immediate parent also collides, the algorithm climbs further."""
    items = [
        ("data", ["alpha", "shared"]),
        ("data", ["beta", "shared"]),
    ]
    # depth=1 collides on "shared"; depth=2 differentiates by alpha vs beta.
    assert _disambiguate_ids(items) == [
        "alpha__shared__data",
        "beta__shared__data",
    ]


def test_disambiguate_ids_root_level_file_keeps_bare_stem() -> None:
    """A file at the dataset root has no parent components; if its stem
    collides with a nested file's stem, the nested file is the one that
    grows a prefix."""
    items = [("data", []), ("data", ["topic_a"])]
    out = _disambiguate_ids(items)
    # The root file falls back to the bare stem; the nested one prefixes.
    assert out == ["data", "topic_a__data"]


def test_disambiguate_ids_preserves_input_order() -> None:
    """Returned list is parallel to the input list (positionally)."""
    items = [
        ("a", ["x"]),
        ("b", ["x"]),
        ("a", ["y"]),
    ]
    assert _disambiguate_ids(items) == ["x__a", "b", "y__a"]


def test_make_field_id_unique_column_returns_bare_id() -> None:
    used: set = set()
    assert make_field_id("rs1", "age", used) == "rs1/age"
    assert "rs1/age" in used


def test_make_field_id_collision_appends_numeric_suffix() -> None:
    """Two distinct column names that sanitize to the same string get
    disambiguated by the same numeric suffix convention as record sets."""
    used: set = set()
    first = make_field_id("rs1", "Age>30", used)
    second = make_field_id("rs1", "Age 30", used)
    assert first == "rs1/Age_30"
    assert second == "rs1/Age_30__2"
    # Both ids are recorded so a third collision continues the sequence.
    third = make_field_id("rs1", "Age=30", used)
    assert third == "rs1/Age_30__3"


def test_normalize_array_shape_accepts_tuple_and_bare_forms() -> None:
    """Tuple-style shapes (numpy.shape repr) coerce to mlc-accepted form."""
    assert normalize_array_shape(ARRAY_SHAPE_UNKNOWN_1D) == "-1"
    assert normalize_array_shape("-1") == "-1"
    assert normalize_array_shape("(-1,)") == "-1"
    assert normalize_array_shape("(-1, -1)") == "-1,-1"
    assert normalize_array_shape("(28, 28)") == "28,28"
    assert normalize_array_shape("28,28") == "28,28"
    assert normalize_array_shape("-1,-1,3") == "-1,-1,3"


def test_shard_template_masks_only_a_separated_index() -> None:
    """Digits fused to letters name the table; only the index is the shard."""
    assert shard_template("assay1-part-000.parquet") == "assay1-part-<N>.parquet"
    assert shard_template("assay2-part-001.parquet") == "assay2-part-<N>.parquet"


def test_a_lone_index_is_still_masked() -> None:
    assert shard_template("part-00001-abc.parquet") == "part-<N>-abc.parquet"
    assert shard_template("000.parquet") == "<N>.parquet"
    assert shard_template("readings.parquet") is None


def test_allocate_record_set_ids_derives_one_id_per_suffix() -> None:
    """A handler emitting several record sets per file needs all of them
    unique, not just the file's own base. The stem comes from ``Path.stem``,
    not from ``get_clean_record_name``, whose hardcoded extension list carries
    neither ``.soft`` nor ``.jsonl``."""
    allocated = allocate_record_set_ids(
        metas("GSE1_family.soft"), ["series", "samples"]
    )

    assert allocated == [
        {"series": "GSE1_family_series", "samples": "GSE1_family_samples"}
    ]


def test_allocate_record_set_ids_separates_the_same_basename_by_parent() -> None:
    allocated = allocate_record_set_ids(metas("a/data.soft", "b/data.soft"), ["series"])

    assert allocated == [{"series": "a__data_series"}, {"series": "b__data_series"}]


def test_a_real_file_keeps_the_bare_name_over_a_derived_one() -> None:
    """Every base is reserved before any suffix is allocated, so the file
    genuinely named ``x_series`` wins it whichever order the batch arrives in."""
    allocated = allocate_record_set_ids(metas("x.soft", "x_series.soft"), ["series"])

    assert allocated == [{"series": "x_series__2"}, {"series": "x_series_series"}]


def test_two_derived_ids_cannot_collide_and_the_winner_is_not_batch_order() -> None:
    """``x.soft`` derives ``x_a``, which is also ``x_a.soft``'s base, and both
    files derive ``x_a_a``. Every collision is settled, and which file keeps
    the contested id follows from the paths — batch order is rglob order, so
    allocating in it would make the ids depend on discovery."""
    forward = allocate_record_set_ids(metas("x.soft", "x_a.soft"), ["a", "a_a"])
    backward = allocate_record_set_ids(metas("x_a.soft", "x.soft"), ["a", "a_a"])

    ids = [rs_id for per_file in forward for rs_id in per_file.values()]
    assert len(ids) == len(set(ids)) == 4
    assert forward == list(reversed(backward))


def test_allocate_record_set_ids_sanitizes_what_it_is_given() -> None:
    allocated = allocate_record_set_ids(metas("a file.soft"), ["sample table"])

    assert allocated == [{"sample table": "a_file_sample_table"}]


def test_allocation_does_not_depend_on_batch_order() -> None:
    """Batch order is rglob order, which is fixed within a process — so no
    determinism test elsewhere would catch an id that depends on it.

    ``a b`` and ``a@b`` sanitize to the same thing, so parent components cannot
    separate these two and a numeric suffix has to. Which file takes it must
    follow from the paths, not from which was discovered first.
    """
    forward = allocate_record_set_ids(metas("a b/GSE.soft", "a@b/GSE.soft"), ["series"])
    reversed_ = allocate_record_set_ids(
        metas("a@b/GSE.soft", "a b/GSE.soft"), ["series"]
    )

    assert forward == list(reversed(reversed_))


def test_numeric_identifier_collisions_start_at_two() -> None:
    assert _disambiguate_ids([("data", []), ("data", []), ("data", [])]) == [
        "data",
        "data__2",
        "data__3",
    ]


def prefix_lines(payload: bytes, limit: int, **kwargs) -> PrefixLines:
    return PrefixLines(io.BytesIO(payload), limit, **kwargs)


def test_prefix_lines_yields_one_line_per_line_ending() -> None:
    assert list(prefix_lines(b"one\ntwo\nthree\n", 1024)) == ["one", "two", "three"]


def test_prefix_lines_strips_the_carriage_return_of_a_crlf_file() -> None:
    assert list(prefix_lines(b"one\r\ntwo\r\n", 1024)) == ["one", "two"]


def test_prefix_lines_decodes_a_stray_byte_rather_than_refusing_it() -> None:
    """A byte outside UTF-8 in a comment is not a reason to lose the file."""
    (line,) = list(prefix_lines(b"caf\xe9\n", 1024))

    assert line.startswith("caf")


def test_prefix_lines_delivers_the_tail_of_a_file_with_no_last_line_ending() -> None:
    """Where a writer that closes the file straight after its last line leaves
    it, and the class of bug that used to lose that line."""
    assert list(prefix_lines(b"one\ntwo", 1024)) == ["one", "two"]


def test_prefix_lines_drops_a_tail_the_bound_cut_in_half() -> None:
    """Half a line is not a line, and nothing may be read off it."""
    reader = prefix_lines(b"one\ntwo\nthree\n", 9)

    assert list(reader) == ["one", "two"]
    assert reader.bounded is True


def test_prefix_lines_says_when_the_stream_ended_before_the_bound() -> None:
    reader = prefix_lines(b"one\ntwo\n", 1024)
    list(reader)

    assert reader.bounded is False


def test_prefix_lines_reports_what_it_pulled_and_what_it_holds() -> None:
    """The two numbers a caller owing a refusal before the line ends needs."""
    seen: list = []
    reader = prefix_lines(
        b"one\ntwo", 1024, on_chunk=lambda read, pending: seen.append((read, pending))
    )
    list(reader)

    assert seen == [(7, 3)]
    assert reader.read == 7


def test_decode_line_drops_only_the_carriage_return_of_the_line_ending() -> None:
    """One CRLF is one line ending. A carriage return in front of it is a byte
    the line carries, and stripping the run took content off the line."""
    assert decode_line(b"a\r\r") == "a\r"


def test_prefix_lines_keeps_a_last_line_that_ends_exactly_on_the_bound() -> None:
    """The stream ended where the bound sits, so nothing was cut in half: the
    tail is a whole line and the read was not stopped by the bound."""
    reader = prefix_lines(b"abc\ndefg", 8)

    assert list(reader) == ["abc", "defg"]
    assert reader.bounded is False


def test_prefix_lines_drops_a_last_line_the_bound_stopped_one_byte_short() -> None:
    """One byte less of bound, and the same tail is a fragment."""
    reader = prefix_lines(b"abc\ndefg", 7)

    assert list(reader) == ["abc"]
    assert reader.bounded is True


class _CountingBytes(io.BytesIO):
    """A stream that remembers how many bytes were pulled through it."""

    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.read_bytes = 0

    def read(self, size: int = -1) -> bytes:
        data = super().read(size)
        self.read_bytes += len(data)
        return data


def test_prefix_lines_never_pulls_more_than_one_byte_past_the_bound() -> None:
    """The one byte is what tells the end of the stream from the bound. Past it
    a bounded read would be reading the file it exists not to read."""
    stream = _CountingBytes(b"x" * 1024)
    reader = PrefixLines(stream, 8)

    assert list(reader) == []
    assert reader.bounded is True
    assert stream.read_bytes <= 8 + 1


def test_plural_adds_an_s_to_a_regular_noun() -> None:
    assert (plural(1, "chain"), plural(2, "chain")) == ("1 chain", "2 chains")


def test_plural_takes_the_plural_of_an_irregular_noun() -> None:
    """``polymer entitys`` is what the trailing ``s`` makes of the one noun the
    structure handlers count that does not take one."""
    assert plural(2, "polymer entity", "polymer entities") == "2 polymer entities"


def test_plural_ignores_the_irregular_form_for_a_count_of_one() -> None:
    assert plural(1, "polymer entity", "polymer entities") == "1 polymer entity"


def test_deposited_states_the_date_a_file_wrote() -> None:
    """Both spellings, because the two structure handlers report what their own
    format writes rather than converting either."""
    assert deposited("12-JAN-98") == "deposited 12-JAN-98"
    assert deposited("1998-01-12") == "deposited 1998-01-12"


def test_deposited_states_nothing_without_a_date() -> None:
    assert deposited(None) == "" and deposited("") == ""


def test_determined_joins_the_methods_with_the_resolution() -> None:
    metadata = {
        "experimental_methods": ["X-RAY DIFFRACTION", "NEUTRON DIFFRACTION"],
        "resolution_angstrom": 1.8,
    }

    assert determined(metadata) == ("X-RAY DIFFRACTION, NEUTRON DIFFRACTION at 1.80 A")


def test_determined_states_a_resolution_with_no_method_on_its_own() -> None:
    assert determined({"resolution_angstrom": 2.0}) == "2.00 A resolution"


def test_determined_states_nothing_about_a_structure_that_says_neither() -> None:
    assert determined({}) == ""
