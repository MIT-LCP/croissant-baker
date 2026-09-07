"""Tests for handler utilities."""

from croissant_baker.handlers.utils import (
    ARRAY_SHAPE_UNKNOWN_1D,
    _disambiguate_ids,
    make_field_id,
    normalize_array_shape,
    shard_template,
)


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
    disambiguated by an appended numeric suffix, mirroring how
    pandas.read_csv handles duplicate column headers by default."""
    used: set = set()
    first = make_field_id("rs1", "Age>30", used)
    second = make_field_id("rs1", "Age 30", used)
    assert first == "rs1/Age_30"
    assert second == "rs1/Age_30__1"
    # Both ids are recorded so a third collision continues the sequence.
    third = make_field_id("rs1", "Age=30", used)
    assert third == "rs1/Age_30__2"


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
