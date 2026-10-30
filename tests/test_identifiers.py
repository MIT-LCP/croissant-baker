"""Every @id in a document names one node, or the bake stops and says which."""

from datetime import datetime
from types import SimpleNamespace

import mlcroissant as mlc
import pytest

from croissant_baker.identifiers import _assert_unique_node_ids, serialize_datetime


def _record_set(rs_id: str, *field_ids: str) -> mlc.RecordSet:
    return mlc.RecordSet(
        id=rs_id,
        name=rs_id,
        fields=[mlc.Field(id=f, name=f, data_types="sc:Text") for f in field_ids],
    )


def test_distinct_identifiers_pass() -> None:
    files = [mlc.FileObject(id="data.csv", name="data.csv")]
    _assert_unique_node_ids(files, [_record_set("data", "data/id")])


def test_a_record_set_reusing_a_file_identifier_is_refused() -> None:
    files = [mlc.FileObject(id="data", name="data.csv")]

    with pytest.raises(ValueError, match="'data' is used by both FileObject and"):
        _assert_unique_node_ids(files, [_record_set("data", "data/id")])


def test_a_nested_field_reusing_an_identifier_is_refused() -> None:
    record_set = _record_set("data", "data/meta")
    record_set.fields[0].sub_fields = [
        mlc.Field(id="data", name="inner", data_types="sc:Text")
    ]

    with pytest.raises(ValueError, match="'data' is used by both RecordSet and Field"):
        _assert_unique_node_ids([], [record_set])


def test_nodes_without_an_identifier_are_not_compared() -> None:
    anonymous = [SimpleNamespace(id=None), SimpleNamespace(id=None)]
    _assert_unique_node_ids(anonymous, [])


def test_serialize_datetime_writes_iso_format() -> None:
    assert serialize_datetime(datetime(2024, 1, 2, 3, 4, 5)) == "2024-01-02T03:04:05"


def test_serialize_datetime_refuses_anything_else() -> None:
    with pytest.raises(TypeError, match="not JSON serializable"):
        serialize_datetime({1, 2})
