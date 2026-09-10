"""What a container's structure means: the generic view and the two layouts.

This layer reads through
:class:`~croissant_baker.handlers.hdf5_handler.layouts.Node` and
imports no h5py, so the same code would describe an AnnData Zarr store. The
tests reach it through real HDF5 files, because that is what a user brings,
except for the one that supplies ``Node`` itself and so proves the seam holds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple

import h5py
import pytest

from croissant_baker.handlers.hdf5_handler import layouts, reading
from croissant_baker.sources import make_source

from tests import hdf5_fixtures as fx
from tests.test_hdf5_reading import counting_source


def described(path: Path):
    """``(layout, structure)`` for ``path`` — exactly one of the two is set."""
    with reading.opened(make_source(path)) as root:
        layout = layouts.recognise(root)
        return layout, None if layout else layouts.structure(root)


def structure_of(path: Path) -> layouts.Structure:
    with reading.opened(make_source(path)) as root:
        return layouts.structure(root)


def table_of(layout: layouts.Layout, key: str) -> dict:
    """One table's columns as ``name -> (type, array shape, path)``."""
    table = next(t for t in layout.tables if t.key == key)
    return {c.name: (c.data_type, c.array_shape, c.path) for c in table.columns}


# ---------------------------------------------------------------------------
# The generic view: every leaf dataset, named by its path
# ---------------------------------------------------------------------------

#: The domains this tool already serves, none of which is a table. Exact, so a
#: wrong path, type or shape fails rather than "some columns came back".
GENERIC = {
    "plain": (
        fx.write_plain,
        {
            "top": ("cr:Int16", "4"),
            "group/middle": ("cr:UInt8", "2,3"),
            "group/deeper/bottom": ("cr:Float64", "5,6,7"),
        },
        2,
    ),
    "keras": (
        fx.write_keras,
        {
            "model_weights/dense/kernel:0": ("cr:Float32", "784,128"),
            "model_weights/dense/bias:0": ("cr:Float32", "128"),
            "model_weights/dense_1/kernel:0": ("cr:Float32", "128,10"),
            "model_weights/dense_1/bias:0": ("cr:Float32", "10"),
        },
        3,
    ),
    "netcdf": (
        fx.write_netcdf,
        {
            "time": ("cr:Float64", "12"),
            "lat": ("cr:Float32", "5"),
            "lon": ("cr:Float32", "7"),
            "tas": ("cr:Float32", "12,5,7"),
        },
        0,
    ),
    "matlab": (
        fx.write_matlab,
        {"counts": ("cr:Float64", "2,3"), "label": ("cr:UInt16", "5")},
        0,
    ),
}


@pytest.mark.parametrize(
    ("writer", "expected", "groups"), GENERIC.values(), ids=list(GENERIC)
)
def test_a_file_outside_single_cell_is_described_by_its_datasets(
    writer, expected, groups, tmp_path: Path
) -> None:
    """Through ``recognise`` first, so this also pins that none of them is
    mistaken for a layout."""
    layout, structure = described(writer(tmp_path / "probe.h5"))

    assert layout is None
    assert {c.name: (c.data_type, c.array_shape) for c in structure.columns} == expected
    assert structure.groups == groups
    # In this view the name *is* the path; letting them drift would name a
    # field one thing and locate it at another.
    assert all(column.name == column.path for column in structure.columns)


def test_the_generic_view_never_asks_for_an_attribute(
    monkeypatch, tmp_path: Path
) -> None:
    """It needs none, and this also pins that a node reads none while it is
    being constructed."""

    def refuse(_self, name):
        raise AssertionError(f"the generic view read the attribute {name!r}")

    monkeypatch.setattr(reading.H5Node, "attr", refuse)

    assert len(structure_of(fx.write_keras(tmp_path / "model.h5")).columns) == 4


def test_every_dtype_maps_to_one_croissant_type(tmp_path: Path) -> None:
    """``(type, array shape, note, members)`` per row of the mapping.

    A reference and a variable-length string both report ``kind == 'O'``, and
    Croissant has no type that parts them, so the note does.
    """
    columns = {
        c.name: c for c in structure_of(fx.write_dtypes(tmp_path / "d.h5")).columns
    }

    assert {
        name: (
            c.data_type,
            c.array_shape,
            c.note,
            [(m.name, m.data_type) for m in c.members],
        )
        for name, c in columns.items()
    } == {
        "ints/i8": ("cr:Int8", "2", "", []),
        "ints/i64": ("cr:Int64", "2", "", []),
        "ints/u16": ("cr:UInt16", "2", "", []),
        "floats/f16": ("cr:Float16", "2", "", []),
        "floats/f64": ("cr:Float64", "2", "", []),
        "flag": ("sc:Boolean", "2", "", []),
        # ``arrayShape`` without ``isArray`` is a hard error in mlcroissant,
        # and a scalar has no shape to declare.
        "scalar": ("cr:Int64", "", "", []),
        "text/vlen": ("sc:Text", "2", "", []),
        "text/fixed": ("sc:Text", "2", "", []),
        "opaque/refs": ("sc:Text", "2", "an opaque object reference, not text", []),
        "opaque/ragged": ("cr:Int32", "2,-1", "", []),
        "opaque/bytes": (
            "sc:Text",
            "",
            "opaque bytes the container does not interpret",
            [],
        ),
        "table": (
            "",
            "3",
            "",
            [("index", "sc:Text"), ("age", "cr:Int64"), ("score", "cr:Float32")],
        ),
    }


# ---------------------------------------------------------------------------
# The cap on the generic view
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "described_count", "capped"),
    [
        (20, 20, False),
        (layouts.MAX_DATASETS, layouts.MAX_DATASETS, False),
        (layouts.MAX_DATASETS + 1, layouts.MAX_DATASETS, True),
    ],
    ids=["below", "exactly", "one-past"],
)
def test_the_cap_omits_only_what_it_had_no_room_for(
    count: int, described_count: int, capped: bool, tmp_path: Path
) -> None:
    """A file holding exactly the cap has lost nothing, so it must not claim
    an omission."""
    structure = structure_of(fx.write_many(tmp_path / "many.h5", count))

    assert (len(structure.columns), structure.capped) == (described_count, capped)


def test_a_node_after_the_cap_that_is_not_a_dataset_omits_nothing(
    tmp_path: Path,
) -> None:
    """The boundary a check on the count alone gets wrong: stopping as soon as
    the count reaches the cap reports an omission for any file whose next node
    happens to be a group, however empty."""
    path = tmp_path / "exact_then_group.h5"
    fx.write_many(path, layouts.MAX_DATASETS)
    with h5py.File(path, "a") as f:
        f.create_group("zzz_empty")

    structure = structure_of(path)

    assert (len(structure.columns), structure.capped) == (layouts.MAX_DATASETS, False)


def test_the_cap_bounds_the_read_and_not_only_the_output(tmp_path: Path) -> None:
    """Measured against the same file walked without a cap, so the comparison
    isolates the cap rather than the size of two different files."""
    path = fx.write_many(tmp_path / "many.h5", 4 * layouts.MAX_DATASETS)

    read = {}
    for label, cap in (("capped", layouts.MAX_DATASETS), ("whole", 10**9)):
        source, counters = counting_source(path)
        with reading.opened(source) as root:
            layouts.structure(root, cap=cap)
        read[label] = sum(counter.count for counter in counters)

    assert read["capped"] < read["whole"] * 0.75, read


def test_the_links_are_counted_and_the_external_target_is_not_named(
    tmp_path: Path,
) -> None:
    target = tmp_path / "outside" / "target.h5"
    target.parent.mkdir()
    structure = structure_of(fx.write_links(tmp_path / "links.h5", target))

    # Two external links, one of them dangling: an external link is classified
    # before it is resolved, so the two are the same thing here.
    assert (structure.external, structure.broken) == (2, 1)
    assert [column.name for column in structure.columns] == ["described/real", "soft"]
    assert "target" not in repr(structure)


# ---------------------------------------------------------------------------
# The four layouts, each asserted whole
# ---------------------------------------------------------------------------


def test_current_anndata_becomes_an_obs_table_and_a_var_table(tmp_path: Path) -> None:
    """``column-order`` is authoritative: anndata's own reader iterates it and
    never the group's key order, which HDF5 returns alphabetically.

    An array attaches to the axis that indexes it, so ``X`` (n_obs × n_var) is
    one field of n_var values per row of ``obs`` — stored at the root rather
    than under it, which is why every column states its own path.
    """
    layout, structure = described(fx.write_h5ad(tmp_path / "a.h5ad", 2000, 500))

    assert (structure, layout.name) == (None, layouts.ANNDATA)
    assert [(t.key, t.path, t.rows) for t in layout.tables] == [
        ("obs", "obs", 2000),
        ("var", "var", 500),
    ]
    assert list(table_of(layout, "obs"))[: len(fx.OBS_COLUMNS)] == list(fx.OBS_COLUMNS)
    assert table_of(layout, "obs") == {
        # A plain string column: a ``string-array`` dataset under pandas 2, and
        # the ``nullable-string-array`` group ``nullable_s`` below under
        # pandas 3. Both are shapes anndata 0.13 emits, and both read as text.
        "cell_id": ("sc:Text", "", "obs/cell_id"),
        # A categorical is typed from its categories and never from its codes:
        # those are int8 below 127 categories and int32 at 40 000, so their
        # width is the encoding. The group holding them has no dtype at all.
        "cell_type": ("sc:Text", "", "obs/cell_type"),
        "sex": ("sc:Text", "", "obs/sex"),
        "n_counts": ("cr:Int64", "", "obs/n_counts"),
        "pct_mito": ("cr:Float32", "", "obs/pct_mito"),
        "is_doublet": ("sc:Boolean", "", "obs/is_doublet"),
        "nullable": ("cr:Int64", "", "obs/nullable"),
        "nullable_b": ("sc:Boolean", "", "obs/nullable_b"),
        # nullable-string-array and nullable-integer are typed from ``values``,
        # never from the ``mask`` beside it.
        "nullable_s": ("sc:Text", "", "obs/nullable_s"),
        # ``X`` is a group of data/indices/indptr whose shape is an attribute,
        # and is not derivable from the children's lengths.
        "X": ("cr:Float32", "500", "X"),
        "layers/counts": ("cr:Int32", "500", "layers/counts"),
        "obsm/X_pca": ("cr:Float32", "10", "obsm/X_pca"),
    }
    assert table_of(layout, "var") == {
        "gene_symbol": ("sc:Text", "", "var/gene_symbol"),
        "highly_variable": ("sc:Boolean", "", "var/highly_variable"),
        "varm/PCs": ("cr:Float32", "10", "varm/PCs"),
    }
    # ``_index`` holds the barcodes: a row identifier rather than an
    # annotation, which is why anndata excludes it from ``column-order``.
    assert "_index" not in table_of(layout, "obs")
    # Counted, not described: obsp and varp are graphs over one axis rather
    # than per-row features, uns is arbitrary, and raw is a second X and var.
    assert layout.undescribed == ("obsp", "raw", "uns", "varp")


def test_pre_spec_anndata_is_read_out_of_its_record_array(tmp_path: Path) -> None:
    """Most of what is archived predates the on-disk spec, and anndata still
    reads all three of these: absent root attributes, ``obs`` as one
    compound-dtype dataset, and a categorical written as integer codes beside
    ``uns/<column>_categories``. A handler ignoring them describes the majority
    of what exists as integers.
    """
    layout, _ = described(fx.write_h5ad_compound(tmp_path / "legacy.h5ad"))

    assert layout.name == layouts.ANNDATA
    assert table_of(layout, "obs") == {
        "louvain": ("sc:Text", "", "obs"),
        "n_genes": ("cr:Int64", "", "obs"),
        "X": ("cr:Float32", "6", "X"),
    }
    assert "index" not in table_of(layout, "obs")


def test_a_current_feature_matrix_becomes_features_and_barcodes(
    tmp_path: Path,
) -> None:
    """10x stores ``shape`` as ``[n_features, n_barcodes]``, transposed
    relative to AnnData, and the matrix is CSC with one column per barcode.
    ``indptr`` has one entry per barcode plus a terminator, which is what pins
    the convention: ``scanpy.read_10x_h5`` reads this exact fixture as 200
    observations of 30 variables.
    """
    layout, structure = described(fx.write_tenx(tmp_path / "matrix.h5", 30, 200))

    assert (structure, layout.name) == (None, layouts.TENX)
    assert [(t.key, t.path, t.rows) for t in layout.tables] == [
        ("features", "matrix/features", 30),
        ("barcodes", "matrix", 200),
    ]
    assert list(table_of(layout, "features"))[: len(fx.TENX_FEATURE_COLUMNS)] == list(
        fx.TENX_FEATURE_COLUMNS
    )
    assert table_of(layout, "features") == {
        "id": ("sc:Text", "", "matrix/features/id"),
        "name": ("sc:Text", "", "matrix/features/name"),
        "feature_type": ("sc:Text", "", "matrix/features/feature_type"),
        "genome": ("sc:Text", "", "matrix/features/genome"),
        "matrix": ("cr:Int32", "200", "matrix"),
    }
    assert table_of(layout, "barcodes") == {
        "barcodes": ("sc:Text", "", "matrix/barcodes")
    }


def test_a_legacy_feature_matrix_becomes_genes_and_barcodes(tmp_path: Path) -> None:
    """Cell Ranger v2 wrote one group named for the reference genome, so
    ``genes`` is at ``/GRCh38/genes`` and nothing else in the manifest would
    record the genome.
    """
    path = fx.write_tenx_legacy(tmp_path / "legacy.h5", 30, 200)
    with h5py.File(path, "a") as f:
        f.create_dataset("library_ids", data=[1])

    layout, structure = described(path)

    assert (structure, layout.name) == (None, layouts.TENX)
    assert [t.key for t in layout.tables] == ["genes", "barcodes"]
    assert table_of(layout, "genes") == {
        "genes": ("sc:Text", "", "GRCh38/genes"),
        "gene_names": ("sc:Text", "", "GRCh38/gene_names"),
        "matrix": ("cr:Int32", "200", "GRCh38"),
    }
    assert table_of(layout, "barcodes") == {
        "barcodes": ("sc:Text", "", "GRCh38/barcodes")
    }
    assert layout.undescribed == ("library_ids",)


def test_a_legacy_matrix_is_recognised_over_the_filetype_it_declares(
    tmp_path: Path,
) -> None:
    """Every real Cell Ranger 2 file carries ``filetype = matrix``. Refusing on
    its presence — on the belief that v2 wrote none — made this layout match
    nothing that was ever written."""
    with h5py.File(fx.write_tenx_legacy(tmp_path / "legacy.h5"), "r") as f:
        assert f.attrs["filetype"] == b"matrix"

    layout, _ = described(tmp_path / "legacy.h5")

    assert layout is not None and layout.name == layouts.TENX


def test_a_legacy_matrix_is_recognised_beside_a_group_that_is_not_a_genome(
    tmp_path: Path,
) -> None:
    """Any extra top-level group used to disqualify the file, because the rule
    counted groups rather than genomes."""
    layout, _ = described(fx.write_tenx_legacy_with_a_sibling(tmp_path / "legacy.h5"))

    assert [t.key for t in layout.tables] == ["genes", "barcodes"]
    assert layout.undescribed == ("metadata",)


def test_a_barnyard_file_gives_one_table_pair_per_genome(tmp_path: Path) -> None:
    """Both genomes are wholly present, so both are described, and the keys
    carry the genome only because there is more than one.

    scanpy and DropletUtils refuse this file and Seurat returns one matrix per
    genome; the two that refuse do so because their readers return exactly one
    matrix, which a manifest is not obliged to.
    """
    layout, structure = described(
        fx.write_tenx_barnyard(tmp_path / "barnyard.h5", 8, 6)
    )

    assert (structure, layout.name) == (None, layouts.TENX)
    assert [t.key for t in layout.tables] == [
        "hg19_genes",
        "hg19_barcodes",
        "mm10_genes",
        "mm10_barcodes",
    ]
    assert table_of(layout, "mm10_genes") == {
        "genes": ("sc:Text", "", "mm10/genes"),
        "gene_names": ("sc:Text", "", "mm10/gene_names"),
        "matrix": ("cr:Int32", "6", "mm10"),
    }
    assert {t.rows for t in layout.tables} == {8, 6}
    assert layout.undescribed == ()


def test_a_pre_spec_sparse_x_is_one_column_and_not_its_csr_arrays(
    tmp_path: Path,
) -> None:
    """Pre-0.7 AnnData spelled a sparse group's shape ``h5sparse_shape``, and
    anndata still reads that name. Without it the group declares no shape, and
    ``data``, ``indices`` and ``indptr`` become three columns of ``obs`` —
    the matrix's internals described as observations."""
    layout, structure = described(fx.write_h5ad_h5sparse(tmp_path / "old.h5ad", 20, 6))

    assert (structure, layout.name) == (None, layouts.ANNDATA)
    assert table_of(layout, "obs") == {
        "louvain": ("cr:Int64", "", "obs"),
        "X": ("cr:Float32", "6", "X"),
    }


def test_a_categories_group_types_the_codes_beside_it(tmp_path: Path) -> None:
    """anndata 0.7.0 to 0.7.8 wrote a categorical as an int8 dataset with its
    labels in a ``__categories`` sibling and no encoding of its own. Typing it
    from the codes reports the encoding's width as the column's type."""
    layout, _ = described(fx.write_h5ad_categories_group(tmp_path / "v07.h5ad"))

    assert table_of(layout, "obs") == {
        "cell_type": ("sc:Text", "", "obs/cell_type"),
        "n_counts": ("cr:Int64", "", "obs/n_counts"),
    }


def test_the_categories_store_is_never_a_column_of_its_own(tmp_path: Path) -> None:
    """With no ``column-order`` the names come from the group's keys, and
    ``__categories`` is one of them. It holds other columns' labels; anndata
    reserves the name, so excluding it can drop nothing real."""
    layout, _ = described(fx.write_h5ad_categories_unordered(tmp_path / "v07.h5ad"))

    assert set(table_of(layout, "obs")) == {"cell_type", "n_counts"}


def test_a_null_dataspace_does_not_cost_the_file_its_description(
    tmp_path: Path,
) -> None:
    """``h5py.Empty`` is legal HDF5 and reports ``shape is None``. One such
    dataset used to raise out of the whole extraction, taking every other
    dataset in the file with it. It is described as a scalar."""
    structure = structure_of(fx.write_null_dataspace(tmp_path / "null.h5"))

    assert {c.name: (c.data_type, c.array_shape) for c in structure.columns} == {
        "empty": ("cr:Float64", ""),
        "g/also_empty": ("cr:Int32", ""),
        "real": ("cr:Int64", "3"),
    }


def test_the_tag_key_list_is_not_a_feature_column(tmp_path: Path) -> None:
    """``_all_tag_keys`` names the optional feature columns rather than being
    one. The length filter alone misses it on a file with no ``id``, which
    declares no feature count to filter against."""
    path = tmp_path / "noid.h5"
    fx.write_tenx(path, 5, 4)
    with h5py.File(path, "a") as f:
        del f["matrix/features/id"]

    layout, _ = described(path)

    assert "_all_tag_keys" not in table_of(layout, "features")


# ---------------------------------------------------------------------------
# A partial match is described for what it holds, never claimed as a layout
# ---------------------------------------------------------------------------

#: Each nearly matches a layout, and each must fall to the generic view with
#: the dataset it really holds named there.
MALFORMED = {
    "no-features": (fx.write_tenx_headless, "matrix/barcodes"),
    "features-not-a-group": (fx.write_tenx_dataset_features, "matrix/features"),
    "no-matrix": (fx.write_tenx_featureless_matrix, "matrix/features/id"),
    "foreign-filetype": (fx.write_tenx_foreign_filetype, "GRCh38/genes"),
    "width-scalar": (fx.write_tenx_legacy_widthless, "GRCh38/genes"),
    "width-no-columns": (
        lambda path: fx.write_tenx_legacy_widthless(path, []),
        "GRCh38/genes",
    ),
}


@pytest.mark.parametrize(("writer", "present"), MALFORMED.values(), ids=list(MALFORMED))
def test_a_partial_match_falls_to_the_generic_view(
    writer, present: str, tmp_path: Path
) -> None:
    """A field standing in for an absent array would name something the file
    does not hold, and an empty ``array_shape`` would call a matrix of unknown
    width a scalar column."""
    layout, structure = described(writer(tmp_path / "probe.h5"))

    assert layout is None
    assert present in {column.name for column in structure.columns}


# ---------------------------------------------------------------------------
# The seam: the layout reader is given a Node, not a file
# ---------------------------------------------------------------------------


@dataclass
class FakeNode:
    """A ``Node`` over dicts. What a Zarr reader would supply instead of h5py."""

    name: str = ""
    path: str = ""
    attributes: Mapping[str, object] = field(default_factory=dict)
    dtype: Optional[str] = None
    shape: Optional[Tuple[int, ...]] = None
    fields: Optional[Tuple[Tuple[str, str], ...]] = None
    unresolved: str = ""
    identity: object = None
    contents: dict = field(default_factory=dict)

    def keys(self) -> Tuple[str, ...]:
        return tuple(self.contents)

    def attr(self, name: str) -> Optional[object]:
        return self.attributes.get(name)

    def child(self, name: str):
        node = self.contents.get(name)
        if node is None:
            return None
        node.name = name
        node.path = f"{self.path}/{name}" if self.path else name
        node.identity = node.path
        return node


def test_a_layout_is_recognised_over_any_node_supplier() -> None:
    """AnnData writes a Zarr store with the same logical layout: the same
    ``encoding-type`` vocabulary, the same ``column-order``, the same
    shape-as-attribute. Nothing here is HDF5, and the reading is identical."""
    root = FakeNode(
        attributes={"encoding-type": "anndata"},
        contents={
            "obs": FakeNode(
                attributes={
                    "encoding-type": "dataframe",
                    "_index": "_index",
                    "column-order": ["sex", "n_counts"],
                },
                contents={
                    "_index": FakeNode(dtype=layouts.STRING, shape=(4,)),
                    "sex": FakeNode(
                        attributes={"encoding-type": "categorical"},
                        contents={
                            "categories": FakeNode(dtype=layouts.STRING, shape=(2,)),
                            "codes": FakeNode(dtype="int8", shape=(4,)),
                        },
                    ),
                    "n_counts": FakeNode(
                        attributes={"encoding-type": "array"}, dtype="int64", shape=(4,)
                    ),
                },
            ),
            "var": FakeNode(
                attributes={
                    "encoding-type": "dataframe",
                    "_index": "_index",
                    "column-order": ["gene"],
                },
                contents={"gene": FakeNode(dtype=layouts.STRING, shape=(3,))},
            ),
            "X": FakeNode(
                attributes={"encoding-type": "csr_matrix", "shape": [4, 3]},
                contents={"data": FakeNode(dtype="float32", shape=(12,))},
            ),
        },
    )

    layout = layouts.recognise(root)

    assert layout is not None and layout.name == layouts.ANNDATA
    assert table_of(layout, "obs") == {
        "sex": ("sc:Text", "", "obs/sex"),
        "n_counts": ("cr:Int64", "", "obs/n_counts"),
        "X": ("cr:Float32", "3", "X"),
    }


def test_the_layout_reader_depends_on_nothing_but_the_standard_library() -> None:
    """No third-party dependency here — h5py above all, since that is what
    keeps the AnnData and 10x knowledge reusable for another container.

    Behaviour cannot enforce it and an indirect import would slip past a search
    for the name, so the imports are read. The bar is the standard library
    rather than one exact set, so this module can still add ``re``.
    """
    import ast
    import sys

    tree = ast.parse(Path(layouts.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])

    assert imported <= set(sys.stdlib_module_names), imported - set(
        sys.stdlib_module_names
    )
