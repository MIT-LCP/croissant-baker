"""Tests for JSONHandler.

All tests use synthetic in-memory data written to tmp_path — no large fixture
files needed.
"""

import gzip
import json
from pathlib import Path

import pytest

from croissant_baker.handlers.json_handler import JSONHandler
from croissant_baker.handlers.registry import find_handler, register_all_handlers
from croissant_baker.sources import make_source


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_json_gz(path: Path, data) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(data, fh)


def _write_jsonl(path: Path, rows: list) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _write_jsonl_gz(path: Path, rows: list) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


_SAMPLE_ROWS = [
    {"id": i, "name": f"item_{i}", "score": float(i) * 1.5, "active": True}
    for i in range(5)
]

_FHIR_BUNDLE = {
    "resourceType": "Bundle",
    "type": "collection",
    "entry": [],
}


class TestCanHandle:
    def test_plain_json_array(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json"
        _write_json(p, _SAMPLE_ROWS)
        assert JSONHandler().claims(make_source(p)) is True

    def test_plain_json_object(self, tmp_path: Path) -> None:
        p = tmp_path / "config.json"
        _write_json(p, {"key": "value"})
        assert JSONHandler().claims(make_source(p)) is True

    def test_fhir_json_rejected(self, tmp_path: Path) -> None:
        """FHIR JSON files (resourceType starts uppercase) must be rejected."""
        p = tmp_path / "bundle.json"
        _write_json(p, _FHIR_BUNDLE)
        assert JSONHandler().claims(make_source(p)) is False

    def test_fhir_lowercase_resource_type_accepted(self, tmp_path: Path) -> None:
        """A file with resourceType starting with lowercase is NOT FHIR — accept it."""
        p = tmp_path / "weird.json"
        _write_json(p, {"resourceType": "notFHIR", "data": 1})
        assert JSONHandler().claims(make_source(p)) is True

    def test_jsonl_accepted(self, tmp_path: Path) -> None:
        p = tmp_path / "data.jsonl"
        _write_jsonl(p, _SAMPLE_ROWS)
        assert JSONHandler().claims(make_source(p)) is True

    # There is no wrapped equivalent of these: the handler is given a logical
    # name, so claims() cannot tell data.jsonl from data.jsonl.gz. That the
    # pipeline strips the wrapper is asserted end to end in
    # test_compression_matrix, over every handler and every compression.


class TestExtractMetadataJsonArray:
    def test_basic_fields_present(self, tmp_path: Path) -> None:
        p = tmp_path / "rows.json"
        _write_json(p, _SAMPLE_ROWS)
        meta = JSONHandler().extract(make_source(p))
        assert meta["file_name"] == "rows.json"
        assert meta["num_rows"] == len(_SAMPLE_ROWS)
        assert meta["num_columns"] == len(_SAMPLE_ROWS[0])
        assert set(meta["columns"]) == {"id", "name", "score", "active"}
        assert meta["encoding_format"] == "application/json"
        assert len(meta["sha256"]) == 64

    def test_column_types(self, tmp_path: Path) -> None:
        p = tmp_path / "typed.json"
        _write_json(p, _SAMPLE_ROWS)
        meta = JSONHandler().extract(make_source(p))
        ct = meta["column_types"]
        assert ct["id"] == "cr:Int64"
        assert ct["name"] == "sc:Text"
        assert ct["score"] == "cr:Float64"
        assert ct["active"] == "sc:Boolean"

    def test_file_size_and_sha256(self, tmp_path: Path) -> None:
        p = tmp_path / "rows.json"
        _write_json(p, _SAMPLE_ROWS)
        meta = JSONHandler().extract(make_source(p))
        assert meta["file_size"] == p.stat().st_size
        assert isinstance(meta["sha256"], str) and len(meta["sha256"]) == 64


class TestExtractMetadataJsonObject:
    def test_single_object_num_rows_one(self, tmp_path: Path) -> None:
        p = tmp_path / "single.json"
        _write_json(p, {"a": 1, "b": "hello"})
        meta = JSONHandler().extract(make_source(p))
        assert meta["num_rows"] == 1
        assert meta["num_columns"] == 2
        assert set(meta["columns"]) == {"a", "b"}

    def test_column_types_single_object(self, tmp_path: Path) -> None:
        p = tmp_path / "single.json"
        _write_json(p, {"ts": "2024-01-15T10:00:00Z", "url": "https://example.com"})
        meta = JSONHandler().extract(make_source(p))
        ct = meta["column_types"]
        assert ct["ts"] == "sc:DateTime"
        assert ct["url"] == "sc:URL"


class TestExtractMetadataJsonl:
    def test_basic_jsonl(self, tmp_path: Path) -> None:
        p = tmp_path / "data.jsonl"
        _write_jsonl(p, _SAMPLE_ROWS)
        meta = JSONHandler().extract(make_source(p))
        assert meta["num_rows"] == len(_SAMPLE_ROWS)
        assert meta["encoding_format"] == "application/jsonl"

    def test_jsonl_skips_blank_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "sparse.jsonl"
        with open(p, "w") as fh:
            fh.write(json.dumps({"x": 1}) + "\n")
            fh.write("\n")
            fh.write(json.dumps({"x": 2}) + "\n")
        meta = JSONHandler().extract(make_source(p))
        assert meta["num_rows"] == 2

    def test_jsonl_skips_malformed_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.jsonl"
        with open(p, "w") as fh:
            fh.write(json.dumps({"x": 1}) + "\n")
            fh.write("not json\n")
            fh.write(json.dumps({"x": 3}) + "\n")
        meta = JSONHandler().extract(make_source(p))
        assert meta["num_rows"] == 2

    def test_jsonl_empty_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        with pytest.raises(ValueError, match="No valid JSON objects"):
            JSONHandler().extract(make_source(p))


class TestExtractMetadataCompressed:
    def test_json_gz_encoding_format(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json.gz"
        _write_json_gz(p, _SAMPLE_ROWS)
        meta = JSONHandler().extract(make_source(p))
        assert meta["encoding_format"] == "application/json"
        assert meta["num_rows"] == len(_SAMPLE_ROWS)

    def test_jsonl_gz_encoding_format(self, tmp_path: Path) -> None:
        p = tmp_path / "data.jsonl.gz"
        _write_jsonl_gz(p, _SAMPLE_ROWS)
        meta = JSONHandler().extract(make_source(p))
        assert meta["encoding_format"] == "application/jsonl"
        assert meta["num_rows"] == len(_SAMPLE_ROWS)


class TestExtractMetadataNested:
    def test_nested_struct(self, tmp_path: Path) -> None:
        rows = [
            {"id": 1, "address": {"city": "Berlin", "zip": "10115"}},
            {"id": 2, "address": {"city": "Munich", "zip": "80331"}},
        ]
        p = tmp_path / "nested.json"
        _write_json(p, rows)
        meta = JSONHandler().extract(make_source(p))
        ct = meta["column_types"]
        assert "address" in ct
        assert isinstance(ct["address"], dict)
        assert "fields" in ct["address"]
        assert "city" in ct["address"]["fields"]

    def test_array_of_primitives(self, tmp_path: Path) -> None:
        rows = [{"tags": ["a", "b"]}, {"tags": ["c"]}]
        p = tmp_path / "arrays.json"
        _write_json(p, rows)
        meta = JSONHandler().extract(make_source(p))
        ct = meta["column_types"]
        assert isinstance(ct["tags"], dict)
        assert ct["tags"].get("is_array") is True

    def test_date_and_url_types(self, tmp_path: Path) -> None:
        rows = [
            {"dob": "1990-05-21", "profile": "https://example.com/user/1"},
        ]
        p = tmp_path / "typed.json"
        _write_json(p, rows)
        meta = JSONHandler().extract(make_source(p))
        ct = meta["column_types"]
        assert ct["dob"] == "sc:Date"
        assert ct["profile"] == "sc:URL"


class TestBuildCroissant:
    def _make_meta(self, tmp_path: Path, file_name: str = "data.json") -> dict:
        rows = [{"col_a": 1, "col_b": "hello"}]
        p = tmp_path / file_name
        _write_json(p, rows)
        return JSONHandler().extract(make_source(p))

    def test_returns_empty_distributions(self, tmp_path: Path) -> None:
        meta = self._make_meta(tmp_path)
        dists, _ = JSONHandler().build_croissant([meta], ["file-id-1"])
        assert dists == []

    def test_one_record_set_per_file(self, tmp_path: Path) -> None:
        meta = self._make_meta(tmp_path)
        _, record_sets = JSONHandler().build_croissant([meta], ["file-id-1"])
        assert len(record_sets) == 1

    def test_record_set_has_correct_fields(self, tmp_path: Path) -> None:
        meta = self._make_meta(tmp_path)
        _, record_sets = JSONHandler().build_croissant([meta], ["file-id-1"])
        rs = record_sets[0]
        field_names = {f.name for f in rs.fields}
        assert "col_a" in field_names
        assert "col_b" in field_names

    def test_two_files_two_record_sets(self, tmp_path: Path) -> None:
        meta1 = self._make_meta(tmp_path, "a.json")
        p2 = tmp_path / "b.jsonl"
        _write_jsonl(p2, [{"x": 1}])
        meta2 = JSONHandler().extract(make_source(p2))
        _, record_sets = JSONHandler().build_croissant(
            [meta1, meta2], ["fid-1", "fid-2"]
        )
        assert len(record_sets) == 2

    def test_nested_struct_produces_sub_fields(self, tmp_path: Path) -> None:
        rows = [{"addr": {"city": "Berlin", "zip": "10115"}}]
        p = tmp_path / "nested.json"
        _write_json(p, rows)
        meta = JSONHandler().extract(make_source(p))
        _, record_sets = JSONHandler().build_croissant([meta], ["fid"])
        rs = record_sets[0]
        addr_field = next(f for f in rs.fields if f.name == "addr")
        assert addr_field.sub_fields is not None
        sub_names = {sf.name for sf in addr_field.sub_fields}
        assert "city" in sub_names
        assert "zip" in sub_names


def test_a_fhir_bundle_named_json_goes_to_the_fhir_handler(tmp_path: Path) -> None:
    """``.json`` is the one extension two handlers claim, so precedence between
    them is the only routing question the registry sweep cannot answer."""
    from croissant_baker.handlers.fhir_handler import FHIRHandler

    register_all_handlers()
    path = tmp_path / "bundle.json"
    _write_json(path, _FHIR_BUNDLE)

    assert isinstance(find_handler(path), FHIRHandler)


@pytest.mark.parametrize("compressed", [False, True], ids=["plain", "gzip"])
@pytest.mark.parametrize(
    "data",
    [
        {"metadata": {"resourceType": "Patient"}, "value": 1},
        {"metadata": [{"resourceType": "Patient"}], "value": 1},
    ],
    ids=["nested-object", "nested-array"],
)
def test_nested_resource_type_routes_to_json(tmp_path, compressed, data):
    from croissant_baker.handlers.registry import HandlerRegistry, builtin_handlers
    from tests.helpers import bake_validated, record_sets

    path = tmp_path / ("nested.json.gz" if compressed else "nested.json")
    (_write_json_gz if compressed else _write_json)(path, data)
    selection = HandlerRegistry(builtin_handlers()).select(path)
    assert isinstance(selection.handler, JSONHandler)
    assert selection.handler.extract(selection.source)["num_rows"] == 1
    assert len(record_sets(bake_validated(tmp_path))) == 1


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ([{"resourceType": "Patient"}], True),
        ({"resourceType": "Patient", "padding": "x" * 5000}, False),
        ({"padding": "x" * 5000, "resourceType": "Patient"}, True),
        ({"resourceType": "notFHIR", "data": 1}, True),
        ({"resourceType": "", "data": 1}, True),
        ({"resourceType": 1, "data": 1}, True),
    ],
    ids=["array", "large-fhir", "late-key", "lowercase", "empty", "non-string"],
)
def test_json_sniff_controls(tmp_path, data, expected):
    path = tmp_path / "control.json"
    _write_json(path, data)
    assert JSONHandler().claims(make_source(path)) is expected


@pytest.mark.parametrize("compressed", [False, True], ids=["plain", "gzip"])
@pytest.mark.parametrize("array", [False, True], ids=["fhir", "array"])
def test_resource_type_routing_controls(tmp_path, compressed, array):
    from croissant_baker.handlers.fhir_handler import FHIRHandler
    from croissant_baker.handlers.registry import HandlerRegistry, builtin_handlers

    data = [{"resourceType": "Patient"}] if array else _FHIR_BUNDLE
    path = tmp_path / ("control.json.gz" if compressed else "control.json")
    (_write_json_gz if compressed else _write_json)(path, data)
    selected = HandlerRegistry(builtin_handlers()).select(path)
    assert isinstance(selected.handler, JSONHandler if array else FHIRHandler)
