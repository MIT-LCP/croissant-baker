"""Foreign-key detection: the pure detector and end-to-end cr:references output."""

import json
from pathlib import Path
from types import SimpleNamespace

import mlcroissant as mlc
import pytest

from croissant_baker.metadata_generator import (
    MetadataGenerator,
    _field_named,
    serialize_datetime,
)
from croissant_baker.references import (
    Unlinkable,
    _parent_name_variants,
    detect_foreign_keys,
)

from tests.helpers import cli


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


def test_parent_identified_by_es_plural() -> None:
    """`box_id` resolves a parent named `boxes` (sibilant stem -> es)."""
    links, _ = detect_foreign_keys(
        [
            _rs("boxes", "boxes", ["box_id"]),
            _rs("items", "items", ["box_id"]),
        ]
    )
    assert [link["parent_rs"] for link in links] == ["boxes"]


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
        {
            "column": "subject_id",
            "record_sets": ["admissions", "patients"],
            "reason": Unlinkable.NO_PARENT,
        }
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


def test_no_record_sets_at_all() -> None:
    """The detector is asked to run before anyone counts the record sets."""
    assert detect_foreign_keys([]) == ([], [])


def test_a_column_repeated_in_one_record_set_is_not_shared() -> None:
    """One RecordSet naming a column twice does not make the column shared."""
    links, unresolved = detect_foreign_keys(
        [_rs("a", "a", ["study_id", "study_id"]), _rs("b", "b", ["value"])]
    )
    assert links == []
    assert unresolved == []


# ---------------------------------------------------------------------------
# Determinism of the tie-break
# ---------------------------------------------------------------------------


def test_parent_name_variants_are_an_ordered_list() -> None:
    """The variants are tried in a fixed order, so the tie-break is fixed.

    Regression guard: this returned a set, and with both `study` and `studies`
    in one dataset the parent that won moved with ``PYTHONHASHSEED``. Asserting
    the ordering itself rather than sweeping seeds keeps the guard exact — a
    seed sweep would only catch the flip about half the time per seed.
    """
    variants = _parent_name_variants("study")
    assert isinstance(variants, list)
    assert variants[0] == "study"
    assert variants.index("studys") < variants.index("studies")
    assert _parent_name_variants("box") == ["box", "boxs", "boxes"]


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0), (1, 2, 0)])
def test_exact_stem_wins_when_both_singular_and_plural_exist(order: tuple) -> None:
    """With `study` and `studies` both present, the exact stem is the parent."""
    record_sets = [
        _rs("study", "study", ["study_id", "title"]),
        _rs("studies", "studies", ["study_id", "label"]),
        _rs("samples", "samples", ["sample_id", "study_id"]),
    ]
    links, _ = detect_foreign_keys([record_sets[i] for i in order])
    assert {link["parent_rs"] for link in links} == {"study"}


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0)])
def test_a_name_two_record_sets_share_identifies_neither(order: tuple) -> None:
    """`study.csv` and `study.tsv` are both named `study`, so neither is parent.

    Only ``id`` is disambiguated upstream, so a name can be claimed twice.
    Electing either would make its twin — the same table in another format — a
    foreign-key child of itself.
    """
    record_sets = [
        _rs("study_csv", "study", ["study_id", "title"]),
        _rs("study_tsv", "study", ["study_id", "title"]),
        _rs("samples", "samples", ["sample_id", "study_id"]),
    ]
    links, unresolved = detect_foreign_keys([record_sets[i] for i in order])
    assert links == []
    assert unresolved == [
        {
            "column": "study_id",
            "record_sets": ["samples", "study_csv", "study_tsv"],
            "reason": Unlinkable.NO_PARENT,
        }
    ]


def test_a_refused_link_is_reported_even_when_another_one_survives() -> None:
    """A third table must not hide the refusal the first two produced.

    ``a`` and ``b`` are mutually keyed; ``c`` is an ordinary child of ``b``.
    The refused ``a -> b`` link is reported whether or not ``c -> b`` succeeds.
    """
    links, unresolved = detect_foreign_keys(
        [
            _rs("a", "a", ["a_id", "b_id"]),
            _rs("b", "b", ["b_id", "a_id"]),
            _rs("c", "c", ["c_id", "b_id"]),
        ]
    )
    assert [(link["child_rs"], link["parent_rs"]) for link in links] == [
        ("b", "a"),
        ("c", "b"),
    ]
    assert unresolved == [
        {
            "column": "b_id",
            "record_sets": ["a", "b", "c"],
            "reason": Unlinkable.WOULD_CYCLE,
        }
    ]


def test_a_link_that_would_close_a_cycle_is_refused() -> None:
    """Two mutually-keyed tables keep one direction; the other is reported."""
    links, unresolved = detect_foreign_keys(
        [
            _rs("studies", "studies", ["study_id", "site_id"]),
            _rs("sites", "sites", ["site_id", "study_id"]),
        ]
    )
    assert links == [
        {
            "child_rs": "studies",
            "column": "site_id",
            "parent_rs": "sites",
            "parent_column": "site_id",
        }
    ]
    assert unresolved == [
        {
            "column": "study_id",
            "record_sets": ["sites", "studies"],
            "reason": Unlinkable.WOULD_CYCLE,
        }
    ]


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


@pytest.fixture
def unresolvable_dataset(tmp_path: Path) -> Path:
    """Two tables sharing `subject_id`, with no table named after it."""
    ds = tmp_path / "unresolvable"
    ds.mkdir()
    (ds / "patients.csv").write_text("subject_id,dob\n1,1990-01-01\n")
    (ds / "admissions.csv").write_text("subject_id,hadm_id\n1,10\n")
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


def _fields_by_record_set(meta: dict) -> dict:
    """Fields keyed by RecordSet ``@id`` — names are not unique, ids are."""
    return {rs["@id"]: {f["name"]: f for f in rs["field"]} for rs in meta["recordSet"]}


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

    fields = _fields_by_record_set(json.loads(out.read_text()))
    ref = fields["samples"]["study_id"].get("references")
    assert ref == {"field": {"@id": "studies/study_id"}}
    # The parent's own key is not a self-reference.
    assert "references" not in fields["studies"]["study_id"]


def test_linked_record_sets_can_still_be_read(relational_dataset: Path) -> None:
    """Validation is not readability, so read the records too.

    A reference makes mlcroissant join the two record sets, and a document that
    validates can still fail to produce a row — which is how a reference cycle
    presents. Written inside the dataset so the relative ``contentUrl`` resolves.
    """
    out = relational_dataset / "c.jsonld"
    MetadataGenerator(
        str(relational_dataset),
        name="rel",
        description="d",
        creators=[{"name": "A"}],
        date_published="2024-01-01",
        detect_references=True,
    ).save_metadata(str(out), validate=False)

    dataset = mlc.Dataset(str(out))
    assert len(list(dataset.records("samples"))) == 2


def test_mutually_keyed_tables_stay_readable(tmp_path: Path) -> None:
    """The cycle guard is what keeps a denormalised pair readable.

    Without it both record sets raise ``NetworkXUnfeasible`` from mlcroissant's
    operation graph, while the document still validates.
    """
    ds = tmp_path / "mutual"
    ds.mkdir()
    (ds / "studies.csv").write_text("study_id,site_id\n1,7\n2,8\n")
    (ds / "sites.csv").write_text("site_id,study_id\n7,1\n8,2\n")

    out = ds / "c.jsonld"
    MetadataGenerator(
        str(ds),
        name="mutual",
        description="d",
        creators=[{"name": "A"}],
        date_published="2024-01-01",
        detect_references=True,
    ).save_metadata(str(out), validate=True)

    dataset = mlc.Dataset(str(out))
    assert len(list(dataset.records("studies"))) == 2
    assert len(list(dataset.records("sites"))) == 2


def test_no_references_by_default(relational_dataset: Path) -> None:
    """Detection is opt-in: default output carries no references."""
    meta = _bake(relational_dataset)
    for rs in meta["recordSet"]:
        for field in rs["field"]:
            assert "references" not in field


def test_detection_is_idempotent(relational_dataset: Path) -> None:
    """Baking the same dataset twice in one process produces the same bytes."""

    def dump() -> str:
        return json.dumps(
            _bake(relational_dataset, detect_references=True),
            sort_keys=True,
            default=serialize_datetime,
        )

    assert dump() == dump()


def test_a_missing_field_is_an_error_not_a_dropped_link() -> None:
    """The lookup raises rather than skipping.

    Every name it is given was copied out of a descriptor built from these very
    Field objects, so a miss is a broken invariant — and losing a link quietly
    is how a heuristic feature stops being reviewable.
    """
    record_set = SimpleNamespace(id="studies", fields=[SimpleNamespace(name="title")])
    with pytest.raises(KeyError, match="study_id"):
        _field_named(record_set, "study_id")


# ---------------------------------------------------------------------------
# The dataset the feature was reviewed against
# ---------------------------------------------------------------------------


@pytest.fixture
def mimic_demo_dir() -> Path:
    """Path to the bundled MIMIC-IV demo dataset."""
    path = (
        Path(__file__).parent
        / "data"
        / "input"
        / "mimiciv_demo"
        / "physionet.org"
        / "files"
        / "mimic-iv-demo"
        / "2.2"
    )
    if not path.exists():
        pytest.skip(f"MIMIC-IV demo dataset not found at {path}")
    return path


def test_the_mimic_demo_keeps_the_links_it_was_reviewed_with(
    mimic_demo_dir: Path,
) -> None:
    """Pin the real-dataset numbers, which no synthetic fixture would notice.

    ``subject_id`` is among the unresolved: MIMIC calls that table ``patients``,
    not ``subjects``, and this pass will not link a parent it cannot name. That
    is the conservatism working, and it is worth pinning too — a heuristic that
    quietly starts linking more is the failure mode here.
    """
    gen = MetadataGenerator(str(mimic_demo_dir), name="mimic", detect_references=True)
    gen.generate_metadata()
    report = gen.reference_report

    assert len(report.links) == 14
    assert [key["column"] for key in report.unresolved] == [
        "hadm_id",
        "order_provider_id",
        "stay_id",
        "subject_id",
    ]


# ---------------------------------------------------------------------------
# The report the CLI reads
# ---------------------------------------------------------------------------


def test_no_report_when_the_pass_did_not_run(relational_dataset: Path) -> None:
    """None, not empty: the pass did not run rather than found nothing."""
    gen = MetadataGenerator(str(relational_dataset), name="rel")
    gen.generate_metadata()
    assert gen.reference_report is None


def test_report_is_empty_when_there_is_nothing_to_link(tmp_path: Path) -> None:
    """A single-table dataset still gets a report — an empty one."""
    ds = tmp_path / "lonely"
    ds.mkdir()
    (ds / "studies.csv").write_text("study_id,title\n1,Alpha\n")

    gen = MetadataGenerator(str(ds), name="lonely", detect_references=True)
    gen.generate_metadata()

    report = gen.reference_report
    assert report is not None
    assert report.links == [] and report.unresolved == []
    assert report.summary_lines() == ["Foreign keys: none detected."]


def test_report_names_unresolved_keys_only_when_verbose(
    unresolvable_dataset: Path,
) -> None:
    gen = MetadataGenerator(str(unresolvable_dataset), name="u", detect_references=True)
    gen.generate_metadata()
    report = gen.reference_report

    assert report.summary_lines() == [
        "Foreign keys: 0 link(s); 1 shared key(s) not linked "
        "(re-run with --verbose to name them)."
    ]
    assert report.summary_lines(verbose=True) == [
        "Foreign keys: 0 link(s); 1 shared key(s) not linked.",
        "  subject_id: shared by admissions, patients"
        " — no single record set is named after it",
    ]


def test_report_names_a_refused_cycle_under_verbose(tmp_path: Path) -> None:
    """The cycle refusal reaches the user with a reason of its own."""
    ds = tmp_path / "mutual"
    ds.mkdir()
    (ds / "studies.csv").write_text("study_id,site_id\n1,7\n")
    (ds / "sites.csv").write_text("site_id,study_id\n7,1\n")

    gen = MetadataGenerator(str(ds), name="m", detect_references=True)
    gen.generate_metadata()

    assert gen.reference_report.summary_lines(verbose=True) == [
        "Foreign keys: 1 link(s); 1 shared key(s) not linked.",
        "  study_id: shared by sites, studies"
        " — linking it would close a reference cycle",
    ]


# ---------------------------------------------------------------------------
# The CLI, which is the only thing the user actually sees
# ---------------------------------------------------------------------------


def test_cli_says_nothing_about_references_without_the_flag(
    unresolvable_dataset: Path, tmp_path: Path
) -> None:
    result = cli(unresolvable_dataset, tmp_path / "out.jsonld")

    assert result.exit_code == 0
    assert "Foreign keys" not in result.stdout


def test_cli_reports_unresolved_keys_without_naming_them(
    unresolvable_dataset: Path, tmp_path: Path
) -> None:
    """The default line is fixed-size: a count, and how to see the detail."""
    result = cli(unresolvable_dataset, tmp_path / "out.jsonld", "--detect-references")

    assert result.exit_code == 0
    assert "Foreign keys: 0 link(s); 1 shared key(s) not linked" in result.stdout
    assert "subject_id" not in result.stdout


def test_cli_names_unresolved_keys_under_verbose(
    unresolvable_dataset: Path, tmp_path: Path
) -> None:
    result = cli(
        unresolvable_dataset,
        tmp_path / "out.jsonld",
        "--detect-references",
        "--verbose",
    )

    assert result.exit_code == 0
    assert (
        "  subject_id: shared by admissions, patients"
        " — no single record set is named after it" in result.stdout
    )


def test_cli_states_the_links_it_made(relational_dataset: Path, tmp_path: Path) -> None:
    result = cli(relational_dataset, tmp_path / "out.jsonld", "--detect-references")

    assert result.exit_code == 0
    assert "Foreign keys: 1 link(s)." in result.stdout
