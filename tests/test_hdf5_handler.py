"""What an HDF5 file becomes in the manifest.

One representative per output shape. What each layout *means* is
``tests/test_layouts.py``, and what every handler owes the pipeline is
``tests/test_handler_contract.py``, which sweeps this handler with the rest.
"""

from __future__ import annotations

import json
from pathlib import Path

import h5py
import pytest

from croissant_baker.handlers import layouts
from croissant_baker.handlers.hdf5_handler import HDF5Handler
from croissant_baker.handlers.registry import builtin_handlers, select_handler
from croissant_baker.metadata_generator import MetadataGenerator
from croissant_baker.scan import Outcome
from croissant_baker.sources import make_source

from tests import hdf5_fixtures as fx
from tests.helpers import (
    bake,
    bake_with_report,
    file_objects,
    file_sets,
    record_sets,
    write_wrapped,
)
from tests.test_hdf5 import counting_source


@pytest.fixture
def handler() -> HDF5Handler:
    return HDF5Handler()


def fields_of(record_set: dict) -> dict:
    """The record set's fields, keyed by name."""
    value = record_set.get("field", [])
    return {f["name"]: f for f in (value if isinstance(value, list) else [value])}


def sets_of(dataset: Path) -> dict:
    """Every record set of a bake of ``dataset``, keyed by identifier."""
    return {rs["@id"]: rs for rs in record_sets(bake(dataset))}


def sources(node) -> list:
    """Every ``source`` object anywhere beneath ``node``."""
    found: list = []
    if isinstance(node, dict):
        if "source" in node and isinstance(node["source"], dict):
            found.append(node["source"])
        for value in node.values():
            found.extend(sources(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(sources(item))
    return found


# ---------------------------------------------------------------------------
# Claiming and failing, where the shared contract sweep cannot reach
# ---------------------------------------------------------------------------


def test_a_file_with_the_extension_but_not_the_signature_is_not_claimed(
    handler: HDF5Handler, dataset: Path
) -> None:
    """Better reported as having no handler than failed while being read."""
    path = dataset / "impostor.h5"
    path.write_bytes(b"id,name\n1,Ada\n")

    assert not handler.claims(make_source(path))


def test_a_matlab_file_is_claimed_through_its_user_block(
    handler: HDF5Handler, dataset: Path
) -> None:
    path = fx.write_matlab(dataset / "session.h5")

    assert handler.claims(make_source(path))
    assert select_handler(path).handler is not None


@pytest.mark.parametrize("extension", [".h5", ".h5ad", ".hdf5"])
def test_no_other_handler_claims_an_hdf5_extension(
    extension: str, dataset: Path
) -> None:
    """The shared sweep does not reach this: it feeds each handler the *sample
    filename* of whichever handler owns an extension, so a bare ``.hdf5`` is
    never offered to anyone."""
    path = fx.write_plain(dataset / f"probe{extension}")
    logical = Path(path.name)

    for other in builtin_handlers():
        if isinstance(other, HDF5Handler):
            continue
        source = make_source(path, logical, with_path=other.INPUT_KIND.value == "path")
        assert not other.claims(source), f"{type(other).__name__} claimed {logical}"


def test_a_truncated_file_raises_a_value_error(
    handler: HDF5Handler, dataset: Path
) -> None:
    """h5py raises a plain ``OSError`` for a half-written container, which the
    sweep's garbage bytes do not produce."""
    whole = fx.write_plain(dataset / "whole.h5").read_bytes()
    path = dataset / "half.h5"
    path.write_bytes(whole[: len(whole) // 2])

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path))

    assert "half.h5" in str(caught.value)


# ---------------------------------------------------------------------------
# The generic record set
# ---------------------------------------------------------------------------


def test_an_unrecognised_file_becomes_one_record_set_of_dataset_paths(
    dataset: Path,
) -> None:
    """A bag of unrelated datasets shares no row axis, so the file itself is
    the record."""
    fx.write_keras(dataset / "model.h5")

    (record_set,) = record_sets(bake(dataset))

    assert record_set["@id"] == "model"
    assert sorted(fields_of(record_set)) == [
        "model_weights/dense/bias:0",
        "model_weights/dense/kernel:0",
        "model_weights/dense_1/bias:0",
        "model_weights/dense_1/kernel:0",
    ]
    assert "4 dataset(s) in 3 group(s)" in record_set["description"]
    assert "one record" in record_set["description"].lower()


def test_a_matrix_this_reader_cannot_describe_is_still_described(
    dataset: Path,
) -> None:
    """Nothing is refused, so a file that *nearly* matches a layout must not
    fail either: the coverage report has to say ``described``."""
    fx.write_tenx_legacy_widthless(dataset / "widthless.h5")

    document, report = bake_with_report(dataset)
    (record_set,) = record_sets(document)

    assert [entry.outcome for entry in report.entries] == [Outcome.DESCRIBED]
    assert "GRCh38/genes" in fields_of(record_set)


def test_the_description_says_the_cap_was_reached_without_inventing_a_count(
    dataset: Path,
) -> None:
    """Counting what the cap left behind means the walk the cap exists to
    avoid, so the sentence claims no number."""
    fx.write_many(dataset / "many.h5", 600)

    (record_set,) = record_sets(bake(dataset))

    assert len(fields_of(record_set)) == layouts.MAX_DATASETS
    assert f"cap of {layouts.MAX_DATASETS}" in record_set["description"]
    assert "at least one further dataset" in record_set["description"]
    assert "300 further" not in record_set["description"]


def test_the_description_accounts_for_a_link_it_would_not_follow(
    dataset: Path, tmp_path: Path
) -> None:
    """Without this the datasets behind the link are simply absent and nothing
    says why. The target is a path outside the dataset, so it is never named."""
    fx.write_links(dataset / "links.h5", tmp_path / "elsewhere.h5")

    (record_set,) = record_sets(bake(dataset))

    assert "2 external link" in record_set["description"]
    assert "1 broken link" in record_set["description"]
    assert "elsewhere" not in json.dumps(record_sets(bake(dataset)))


def test_an_empty_container_is_described_as_empty(dataset: Path) -> None:
    """A placeholder file is a fact about the dataset, so it is neither
    refused nor left out."""
    with h5py.File(dataset / "empty.h5", "w"):
        pass

    (record_set,) = record_sets(bake(dataset))

    assert record_set["@id"] == "empty"
    assert "0 dataset(s)" in record_set["description"]
    assert "field" not in record_set


def test_a_compound_dataset_becomes_sub_fields(dataset: Path) -> None:
    fx.write_dtypes(dataset / "probe.h5")

    (record_set,) = record_sets(bake(dataset))
    table = fields_of(record_set)["table"]

    assert [m["name"] for m in table["subField"]] == ["index", "age", "score"]
    assert "dataType" not in table
    # A member has no path of its own; the record array holding it does.
    assert "record array /table" in table["subField"][1]["description"]


# ---------------------------------------------------------------------------
# A recognised layout's tables
# ---------------------------------------------------------------------------


def test_a_table_reaches_the_document_with_its_types_and_shapes(
    dataset: Path,
) -> None:
    """One representative of the shape every recognised layout produces. Which
    columns each layout finds, and why, is ``tests/test_layouts.py``."""
    fx.write_h5ad(dataset / "sample.h5ad", n_obs=2000, n_var=500)

    sets = sets_of(dataset)
    fields = fields_of(sets["sample_obs"])

    assert sorted(sets) == ["sample_obs", "sample_var"]
    assert {name: str(f["dataType"]) for name, f in fields.items()} == {
        "cell_id": "sc:Text",
        "cell_type": "sc:Text",
        "sex": "sc:Text",
        "n_counts": "cr:Int64",
        "pct_mito": "cr:Float32",
        "is_doublet": "sc:Boolean",
        "nullable": "cr:Int64",
        "nullable_b": "sc:Boolean",
        "X": "cr:Float32",
        "layers/counts": "cr:Int32",
        "obsm/X_pca": "cr:Float32",
    }
    assert (fields["X"]["cr:arrayShape"], fields["X"]["cr:isArray"]) == ("500", True)
    assert "cr:isArray" not in fields["n_counts"]

    description = sets["sample_obs"]["description"]
    assert layouts.ANNDATA in description
    assert "2000 row(s), one row per observation" in description
    # Counted rather than described, so nothing in the file goes unmentioned.
    assert "obsp, raw, uns, varp" in description


def test_every_description_states_where_to_find_the_value(dataset: Path) -> None:
    """A field's name does not say where it is, and no convention recovers it:
    ``genes`` is under a group named for the genome, and an array on the obs
    axis is stored at the root rather than under ``/obs``."""
    fx.write_tenx_legacy(dataset / "legacy.h5")
    fx.write_h5ad(dataset / "atlas.h5ad")
    fx.write_keras(dataset / "model.h5")

    sets = sets_of(dataset)
    described = {
        name: field["description"]
        for record_set in sets.values()
        for name, field in fields_of(record_set).items()
    }

    assert "/GRCh38/genes" in described["genes"]
    assert "/GRCh38," in described["matrix"]
    assert "/GRCh38/barcodes" in described["barcodes"]
    assert "/obs/cell_type" in described["cell_type"]
    assert "/obsm/X_pca" in described["obsm/X_pca"]
    assert "/X," in described["X"]
    assert "/model_weights/dense/kernel:0" in described["model_weights/dense/kernel:0"]
    # And the summary a reader sees before opening any field.
    assert "columns at /GRCh38" in sets["legacy_genes"]["description"]
    assert "columns at /obs" in sets["atlas_obs"]["description"]
    assert "columns at /var" in sets["atlas_var"]["description"]


@pytest.mark.parametrize("suffix", ["", ".gz"])
def test_a_description_names_the_file_as_it_sits_on_disk(
    suffix: str, dataset: Path, tmp_path: Path
) -> None:
    """Identifiers come from the logical name, so a wrapped container and its
    plain twin describe one thing. Prose names the file on disk instead."""
    payload = fx.write_h5ad(tmp_path / "atlas.h5ad").read_bytes()
    write_wrapped(dataset, "atlas.h5ad", payload, suffix)
    stored = f"atlas.h5ad{suffix}"

    for record_set in record_sets(bake(dataset)):
        assert stored in record_set["description"]
        for field in fields_of(record_set).values():
            assert stored in field["description"], field["description"]


def test_a_column_nothing_types_still_validates(handler: HDF5Handler) -> None:
    """mlcroissant rejects a field carrying neither a dataType nor sub-fields.
    Driven from a ``Column`` rather than a file, because no writer is known to
    produce one."""
    layout = layouts.Layout(
        layouts.ANNDATA,
        (
            layouts.Table(
                "obs",
                "observation",
                "obs",
                (layouts.Column("mystery", "", path="obs/mystery"),),
            ),
        ),
    )
    meta = {"file_name": "odd.h5ad", "layout": layout, "structure": None}

    (record_set,) = handler.build_croissant([meta], ["odd.h5ad"]).record_sets

    # ``str`` because mlcroissant hands a dataType back as an rdflib URIRef.
    assert [str(t) for t in record_set.fields[0].data_types] == ["sc:Text"]


# ---------------------------------------------------------------------------
# The contract every field keeps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "writer", [fx.write_h5ad, fx.write_tenx_legacy, fx.write_dtypes]
)
def test_every_field_carries_a_source_with_no_extract_and_a_type(
    writer, dataset: Path
) -> None:
    """mlcroissant has no HDF5 reader at any media type, so it validates an
    ``extract`` here and could never execute one — a promise nobody can keep
    that the validator would not catch.
    """
    writer(dataset / "probe.h5")

    document = bake(dataset)
    fields = [f for rs in record_sets(document) for f in fields_of(rs).values()]

    assert fields, "nothing was described at all"
    assert len(sources(document)) >= len(fields)
    for source in sources(document):
        assert "extract" not in source, source
        assert "fileObject" in source
    for field in fields:
        assert "dataType" in field or "subField" in field, field["name"]


@pytest.mark.parametrize(
    ("writer", "forbidden"),
    list(fx.FORBIDDEN_VALUES.items()),
    ids=lambda value: getattr(value, "__name__", ""),
)
def test_no_value_from_inside_the_file_reaches_the_document(
    writer, forbidden, dataset: Path
) -> None:
    """The one document-level privacy invariant. Which values are forbidden
    for which fixture, and why a group *name* is not one of them, is
    ``FORBIDDEN_VALUES``.

    The fields are counted first: an empty document forbids everything.
    """
    writer(dataset / "probe.h5")

    document = bake(dataset)
    fields = [f for rs in record_sets(document) for f in fields_of(rs).values()]

    assert len(fields) >= 3, fields
    for value in forbidden:
        assert value not in json.dumps(document), value


def test_each_file_is_a_file_object_with_no_file_set(dataset: Path) -> None:
    """One HDF5 file is one container; a FileSet over several would claim they
    share a schema."""
    fx.write_plain(dataset / "a.h5")
    fx.write_keras(dataset / "b.h5")

    document = bake(dataset)

    assert len(file_objects(document)) == 2
    assert file_sets(document) == []


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------


def test_files_sharing_a_basename_get_ids_from_their_paths(dataset: Path) -> None:
    """The shape the corpus actually has: one ``filtered_feature_bc_matrix.h5``
    per sample directory. A bare basename would give all three the same record
    set, and in Croissant a collision merges nodes rather than failing.
    """
    for sample in ("GSM1", "GSM2", "GSM3"):
        (dataset / sample).mkdir()
        fx.write_tenx(dataset / sample / "filtered_feature_bc_matrix.h5")

    assert sorted(sets_of(dataset)) == [
        f"GSM{n}__filtered_feature_bc_matrix_{table}"
        for n in (1, 2, 3)
        for table in ("barcodes", "features")
    ]


def test_a_suffixed_id_cannot_collide_with_another_file(dataset: Path) -> None:
    """``sample.h5ad`` wants ``sample_obs``, and a file actually named
    ``sample_obs.h5`` already holds it. The bare base is reserved first, so the
    outcome does not depend on which file the scan reached."""
    fx.write_h5ad(dataset / "sample.h5ad")
    fx.write_plain(dataset / "sample_obs.h5")

    assert sorted(sets_of(dataset)) == ["sample_obs", "sample_obs__2", "sample_var"]


def test_identifiers_do_not_depend_on_the_order_the_files_were_reached(
    handler: HDF5Handler, dataset: Path
) -> None:
    """``rglob`` order differs between two freshly created directories, and a
    handler batch is built in that order. Driven through ``build_croissant``
    with the batch reversed, because two directories built the same way cannot
    show it."""
    fx.write_h5ad(dataset / "sample.h5ad")
    fx.write_plain(dataset / "sample_obs.h5")
    metas, ids = [], []
    for name in ("sample.h5ad", "sample_obs.h5"):
        metas.append(handler.extract(make_source(dataset / name, Path(name))))
        ids.append(name)

    forward = handler.build_croissant(metas, ids).record_sets
    backward = handler.build_croissant(metas[::-1], ids[::-1]).record_sets

    assert sorted(rs.id for rs in forward) == sorted(rs.id for rs in backward)
    assert sorted(rs.id for rs in forward) == [
        "sample_obs",
        "sample_obs__2",
        "sample_var",
    ]


def test_concurrent_extraction_matches_serial(dataset: Path) -> None:
    """h5py serialises every low-level call behind a process-wide lock, so
    concurrency here is correct rather than fast — and the handler must still
    hold no per-call state."""
    for i in range(6):
        fx.write_h5ad(dataset / f"sample{i}.h5ad", n_obs=20, n_var=5)
        fx.write_tenx(dataset / f"matrix{i}.h5", 4, 6)

    def build(workers: int) -> str:
        document = MetadataGenerator(
            dataset_path=str(dataset), name="c", max_workers=workers
        ).generate_metadata()
        return json.dumps(document["recordSet"], sort_keys=True)

    assert build(8) == build(1)


# ---------------------------------------------------------------------------
# Describing does not read the data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("writer", "described_columns"),
    [
        (fx.write_dense, 8),
        (lambda path: fx.write_h5ad(path, 2000, 50, payload=1_000_000), 15),
    ],
    ids=["generic", "anndata"],
)
def test_describing_reads_structure_and_never_the_arrays_it_names(
    writer, described_columns: int, handler: HDF5Handler, tmp_path: Path
) -> None:
    """Criterion 10, on both paths, over datasets whose bytes are really there.

    The columns are counted first, because the measurement means nothing unless
    the arrays were described: a reader that walked past them would read little
    either. Two fixtures that look like they would work do not — an unallocated
    dataset reads back without growing the file, and a root dataset is never
    visited by a recognised AnnData layout.
    """
    path = writer(tmp_path / "probe.h5")
    payload = path.stat().st_size
    assert payload > 7_000_000, payload

    source, counters = counting_source(path)
    meta = handler.extract(source)
    read = sum(counter.count for counter in counters)

    columns = (
        meta["structure"].columns
        if meta["layout"] is None
        else [c for t in meta["layout"].tables for c in t.columns]
    )
    assert len(columns) == described_columns
    assert read < payload / 50, f"read {read} of {payload}"
