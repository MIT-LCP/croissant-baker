"""Foreign-key detection: the pure detector and end-to-end cr:references output."""

import json
from pathlib import Path

import pytest

from croissant_baker.metadata_generator import MetadataGenerator, serialize_datetime
from croissant_baker.references import detect_foreign_keys


# ---------------------------------------------------------------------------
# Pure detector
# ---------------------------------------------------------------------------


def _rs(rid: str, name: str, columns: list) -> dict:
    return {"id": rid, "name": name, "columns": columns}


def test_links_child_to_named_parent_same_column() -> None:
    """A shared <stem>_id links to a parent table named after the stem."""
    links, unresolved = detect_foreign_keys(
        [
            _rs("studies", "studies", ["study_id", "title"]),
            _rs("samples", "samples", ["sample_id", "study_id", "value"]),
        ]
    )
    assert unresolved == []
    assert links == [
        {
            "child_rs": "samples",
            "column": "study_id",
            "parent_rs": "studies",
            "parent_column": "study_id",
        }
    ]


def test_parent_identified_by_plural_y_to_ies() -> None:
    """`study_id` resolves a parent RecordSet named `studies` (y -> ies)."""
    links, _ = detect_foreign_keys(
        [
            _rs("studies", "studies", ["study_id"]),
            _rs("obs", "observations", ["study_id"]),
        ]
    )
    assert [link["parent_rs"] for link in links] == ["studies"]


def test_rails_style_bare_id_parent_not_linked_in_v1() -> None:
    """v1 links same-named shared keys only.

    A Rails-style parent (`study` with a bare `id`) whose child key (`study_id`)
    lives only in the child is intentionally left alone — the key is not shared
    under the same name, so there is nothing to match conservatively.
    """
    links, unresolved = detect_foreign_keys(
        [
            _rs("study", "study", ["id", "title"]),
            _rs("samples", "samples", ["study_id"]),
        ]
    )
    assert links == []
    assert unresolved == []


def test_unresolved_when_no_named_parent() -> None:
    """A shared key with no name-matching parent is reported, not linked."""
    links, unresolved = detect_foreign_keys(
        [
            _rs("patients", "patients", ["subject_id", "dob"]),
            _rs("admissions", "admissions", ["subject_id", "hadm_id"]),
        ]
    )
    assert links == []
    assert unresolved == [
        {"column": "subject_id", "record_sets": ["patients", "admissions"]}
    ]


def test_ignores_non_key_and_unshared_columns() -> None:
    """Non-`_id` columns and keys present in only one table produce nothing."""
    links, unresolved = detect_foreign_keys(
        [
            _rs("a", "a", ["value", "study_id"]),
            _rs("b", "b", ["value", "note"]),  # shares 'value' (not key-like)
        ]
    )
    assert links == []
    assert unresolved == []


def test_bare_id_is_not_a_foreign_key() -> None:
    """A shared bare `id` column is too generic to treat as a foreign key."""
    links, unresolved = detect_foreign_keys(
        [_rs("a", "a", ["id"]), _rs("b", "b", ["id"])]
    )
    assert links == []
    assert unresolved == []


def test_single_record_set_has_no_links() -> None:
    links, unresolved = detect_foreign_keys([_rs("a", "a", ["study_id"])])
    assert links == [] and unresolved == []


# ---------------------------------------------------------------------------
# End-to-end through MetadataGenerator
# ---------------------------------------------------------------------------


@pytest.fixture
def relational_dataset(tmp_path: Path) -> Path:
    ds = tmp_path / "relational"
    ds.mkdir()
    (ds / "studies.csv").write_text("study_id,title\n1,Alpha\n2,Beta\n")
    (ds / "samples.csv").write_text("sample_id,study_id,value\n10,1,0.5\n11,2,0.7\n")
    return ds


def _bake(ds: Path, **kwargs) -> dict:
    return MetadataGenerator(
        str(ds),
        name="rel",
        description="d",
        creators=[{"name": "A"}],
        date_published="2024-01-01",
        **kwargs,
    ).generate_metadata()


def test_detect_references_emits_and_validates(
    relational_dataset: Path, tmp_path: Path
) -> None:
    """With detection on, the child key references the parent and output validates."""
    out = tmp_path / "c.jsonld"
    gen = MetadataGenerator(
        str(relational_dataset),
        name="rel",
        description="d",
        creators=[{"name": "A"}],
        date_published="2024-01-01",
        detect_references=True,
    )
    gen.save_metadata(str(out), validate=True)  # must not raise

    meta = json.loads(out.read_text())
    fields = {
        rs["name"]: {f["name"]: f for f in rs["field"]} for rs in meta["recordSet"]
    }
    ref = fields["samples"]["study_id"].get("references")
    assert ref == {"field": {"@id": "studies/study_id"}}
    # The parent's own key is not a self-reference.
    assert "references" not in fields["studies"]["study_id"]


def test_no_references_by_default(relational_dataset: Path) -> None:
    """Detection is opt-in: default output carries no references."""
    meta = _bake(relational_dataset)
    for rs in meta["recordSet"]:
        for field in rs["field"]:
            assert "references" not in field


def test_detection_is_deterministic(relational_dataset: Path) -> None:
    """Reference detection does not introduce run-to-run variation."""

    def dump() -> str:
        return json.dumps(
            _bake(relational_dataset, detect_references=True),
            sort_keys=True,
            default=serialize_datetime,
        )

    assert dump() == dump()
