"""HDF5 files to describe, written by hand against what the real writers emit.

Hand-built rather than produced by ``anndata`` or ``cellranger``, which are not
dependencies of this package, and small enough to commit. What each writer
reproduces is not a reading of the specs but a dump of a real file, recorded in
``tests/data/input/hdf5_demo/README.md`` beside the command that produced it.

Reading a spec was not enough, and the differences were load-bearing: real 10x
stores every string fixed-length rather than variable-length, writes ``indices``
as ``int64``, and — the one that mattered — a real Cell Ranger 2 file carries
``filetype = matrix`` at the root, which an earlier version of this module
omitted and the reader therefore refused. Legacy files come from PyTables, so
every object also carries ``CLASS``, ``VERSION``, ``TITLE`` and ``FILTERS``,
and ``TITLE`` is sometimes a null dataspace — the shape that used to cost a
file its whole description.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import h5py
import numpy as np

#: Variable-length UTF-8, which is how ``anndata`` stores every string.
VLEN = h5py.string_dtype(encoding="utf-8")


def _fixed(values: Sequence[str]):
    """ASCII padded to the longest value, which is how Cell Ranger stores text.

    Not :data:`VLEN`. Every string in a real 10x file is fixed-length — ``S18``
    for a barcode, ``S15`` for a feature id — and the two take different
    branches of the reader's dtype normalisation.
    """
    return np.array([v.encode("ascii") for v in values], dtype="S")


def _pytables(node, klass: str, version: str = "1.1", *, titled: bool = False) -> None:
    """The attributes PyTables stamps on every object it writes.

    Cell Ranger 2 wrote through PyTables, so a real legacy file carries these
    on every group and dataset. ``TITLE`` on a dataset is a *null dataspace* —
    an attribute holding no value at all — which is worth having in a fixture.
    """
    node.attrs["CLASS"] = np.bytes_(klass.encode())
    node.attrs["VERSION"] = np.bytes_(version.encode())
    node.attrs["TITLE"] = h5py.Empty("S1") if titled else np.bytes_(b"")
    if klass == "GROUP":
        node.attrs["FILTERS"] = np.int64(65793)


#: The 10x feature table's columns, in the order Cell Ranger writes them.
TENX_FEATURE_COLUMNS = ("id", "name", "feature_type", "genome")

#: ``obs`` columns of :func:`write_h5ad`, in ``column-order``. One per encoding
#: a column can take, and both encodings a *string* column can take.
#:
#: Which of the two a real file has turns on pandas, not on anndata: under
#: pandas 2 a plain string column is written as a ``string-array`` dataset,
#: and under pandas 3 — where ``StringDtype`` became the default — the same
#: column is written as a ``nullable-string-array`` group. Both are shapes
#: anndata 0.13 emits, so the fixture carries both.
OBS_COLUMNS = (
    "cell_id",  # string-array dataset: a plain string column under pandas 2
    "cell_type",  # low cardinality: anndata converts these to categorical
    "sex",
    "n_counts",
    "pct_mito",
    "is_doublet",
    "nullable",
    "nullable_b",
    "nullable_s",  # nullable-string-array group: the same column under pandas 3
)

VAR_COLUMNS = ("gene_symbol", "highly_variable")


# ---------------------------------------------------------------------------
# AnnData, as written since the on-disk spec (encoding-type everywhere)
# ---------------------------------------------------------------------------


def _encoded(node, encoding: str, version: str = "0.2.0") -> None:
    node.attrs["encoding-type"] = encoding
    node.attrs["encoding-version"] = version


def _strings(group, name: str, values: Iterable[str], encoding="string-array"):
    dataset = group.create_dataset(name, data=list(values), dtype=VLEN)
    if encoding:
        _encoded(dataset, encoding)
    return dataset


def _array(group, name: str, data):
    dataset = group.create_dataset(name, data=data)
    _encoded(dataset, "array")
    return dataset


def _categorical(parent, name: str, categories: Sequence[str], codes):
    """A group of ``categories`` and ``codes``. The codes' width is the
    encoding — int8 below 127 categories — never the column's type."""
    group = parent.create_group(name)
    _encoded(group, "categorical")
    group.attrs["ordered"] = False
    _strings(group, "categories", categories)
    _array(group, "codes", np.asarray(codes, dtype="int8"))
    return group


def _nullable(parent, name: str, values, encoding: str):
    group = parent.create_group(name)
    _encoded(group, encoding, "0.1.0")
    if encoding == "nullable-string-array":
        group.attrs["na-value"] = "NaN"
        _strings(group, "values", values)
    else:
        _array(group, "values", values)
    _array(group, "mask", np.zeros(len(values), dtype=bool))
    return group


def _csr(parent, name: str, shape, dtype="float32", nnz=None):
    """A sparse group. The shape is an *attribute* here; 10x makes it a child."""
    rows, cols = shape
    nnz = rows * cols if nnz is None else nnz
    group = parent.create_group(name)
    _encoded(group, "csr_matrix", "0.1.0")
    group.attrs["shape"] = np.asarray(shape, dtype="int64")
    group.create_dataset("data", data=np.ones(nnz, dtype=dtype))
    group.create_dataset("indices", data=np.zeros(nnz, dtype="int32"))
    group.create_dataset("indptr", data=np.linspace(0, nnz, rows + 1).astype("int32"))
    return group


def _dict(parent, name: str):
    group = parent.create_group(name)
    _encoded(group, "dict", "0.1.0")
    return group


def _dataframe(parent, name: str, columns: Sequence[str], index: Sequence[str]):
    """A dataframe group whose ``_index`` is a ``string-array`` dataset.

    A dataset, not a group: that is what anndata writes for an index of plain
    strings. The group form belongs to pandas' nullable string dtype, and
    :data:`OBS_COLUMNS` carries one of those separately.
    """
    group = parent.create_group(name)
    _encoded(group, "dataframe")
    group.attrs["_index"] = "_index"
    group.attrs["column-order"] = np.asarray(columns, dtype=object)
    _strings(group, "_index", index)
    return group


def write_h5ad(path: Path, n_obs: int = 200, n_var: int = 50, payload: int = 0) -> Path:
    """An ``.h5ad`` carrying one column per encoding a column can take.

    ``payload`` adds that many float64 elements to ``obsm``, so the bytes are
    both really on disk and inside what the description names. An unallocated
    dataset would not do: it can be read without growing the file, and neither
    would a root dataset, which a recognised AnnData layout never visits.
    """
    with h5py.File(path, "w") as f:
        _encoded(f, "anndata", "0.1.0")

        obs = _dataframe(f, "obs", OBS_COLUMNS, [f"bc_{i}" for i in range(n_obs)])
        _strings(obs, "cell_id", [f"cell_{i}" for i in range(n_obs)])
        _categorical(obs, "cell_type", ["B", "NK", "T"], np.arange(n_obs) % 3)
        _categorical(obs, "sex", ["female", "male"], np.arange(n_obs) % 2)
        _array(obs, "n_counts", np.arange(n_obs, dtype="int64"))
        _array(obs, "pct_mito", np.linspace(0, 1, n_obs).astype("float32"))
        _array(obs, "is_doublet", np.zeros(n_obs, dtype=bool))
        _nullable(obs, "nullable", np.arange(n_obs, dtype="int64"), "nullable-integer")
        _nullable(obs, "nullable_b", np.zeros(n_obs, dtype=bool), "nullable-boolean")
        _nullable(
            obs,
            "nullable_s",
            [f"lot_{i}" for i in range(n_obs)],
            "nullable-string-array",
        )

        var = _dataframe(f, "var", VAR_COLUMNS, [f"ENSG{i:08d}" for i in range(n_var)])
        _strings(var, "gene_symbol", [f"GENE{i}" for i in range(n_var)])
        _array(var, "highly_variable", np.zeros(n_var, dtype=bool))

        _csr(f, "X", (n_obs, n_var))
        _csr(_dict(f, "layers"), "counts", (n_obs, n_var), dtype="int32")
        obsm = _dict(f, "obsm")
        _array(obsm, "X_pca", np.zeros((n_obs, 10), dtype="float32"))
        _array(_dict(f, "varm"), "PCs", np.zeros((n_var, 10), dtype="float32"))
        _csr(_dict(f, "obsp"), "connectivities", (n_obs, n_obs), "float64", nnz=0)
        _csr(_dict(f, "varp"), "corr", (n_var, n_var), "float64", nnz=0)

        uns = _dict(f, "uns")
        note = uns.create_dataset("note", data="hello", dtype=VLEN)
        _encoded(note, "string")

        raw = f.create_group("raw")
        _encoded(raw, "raw", "0.1.0")
        _csr(raw, "X", (n_obs, n_var))

        if payload:
            _array(obsm, "payload", np.ones((n_obs, payload // n_obs), dtype="float64"))
    return path


def write_h5ad_compound(path: Path, n_obs: int = 20) -> Path:
    """Pre-spec AnnData: no root attributes, ``obs`` a compound-dtype dataset.

    This is what the archives hold. anndata still reads it, because a missing
    ``encoding-type`` is its empty spec rather than an error.
    """
    obs = np.zeros(
        n_obs, dtype=[("index", "S12"), ("louvain", "i8"), ("n_genes", "i8")]
    )
    var = np.zeros(6, dtype=[("index", "S12"), ("n_cells", "i8")])
    with h5py.File(path, "w") as f:
        f["obs"] = obs
        f["var"] = var
        f["X"] = np.zeros((n_obs, 6), dtype="float32")
        # The legacy categorical: integer codes here, labels in a uns sibling.
        f.create_dataset("uns/louvain_categories", data=["0", "1", "2"], dtype=VLEN)
    return path


def write_h5ad_h5sparse(path: Path, n_obs: int = 20, n_var: int = 6) -> Path:
    """Pre-0.7 AnnData: ``X`` a sparse group under the ``h5sparse`` names.

    ``h5sparse_shape`` rather than ``shape``, after the library anndata's sparse
    support came from. anndata still reads it. Without that fallback the group
    declares no shape, and its three CSR arrays become three columns of ``obs``.
    """
    with h5py.File(path, "w") as f:
        f["obs"] = np.zeros(n_obs, dtype=[("index", "S12"), ("louvain", "i8")])
        f["var"] = np.zeros(n_var, dtype=[("index", "S12")])
        nnz = n_obs
        x = f.create_group("X")
        x.attrs["h5sparse_format"] = np.bytes_(b"csr")
        x.attrs["h5sparse_shape"] = np.asarray([n_obs, n_var], dtype="int64")
        x.create_dataset("data", data=np.ones(nnz, dtype="float32"))
        x.create_dataset("indices", data=np.zeros(nnz, dtype="int32"))
        x.create_dataset("indptr", data=np.arange(n_obs + 1, dtype="int32"))
    return path


def write_h5ad_categories_group(path: Path, n_obs: int = 12) -> Path:
    """AnnData 0.7.0 to 0.7.8: a categorical as codes beside ``__categories``.

    No per-column ``encoding-type``, and the link to the labels is a
    ``categories`` object *reference*. The reference is written because real
    files carry it, but it is not what the reader matches on: a reference names
    something rather than holding a value, so the ``Node`` interface reports it
    absent. The sibling's name is what the reader follows, and anndata's writer
    composes that exact path.
    """
    with h5py.File(path, "w") as f:
        _encoded(f, "anndata", "0.1.0")
        obs = f.create_group("obs")
        _encoded(obs, "dataframe")
        obs.attrs["_index"] = "_index"
        obs.attrs["column-order"] = np.asarray(("cell_type", "n_counts"), dtype=object)
        obs.create_dataset("_index", data=[f"bc_{i}" for i in range(n_obs)], dtype=VLEN)
        labels = obs.create_group("__categories").create_dataset(
            "cell_type", data=["B", "T"], dtype=VLEN
        )
        labels.attrs["ordered"] = False
        codes = obs.create_dataset(
            "cell_type", data=(np.arange(n_obs) % 2).astype("int8")
        )
        codes.attrs["categories"] = labels.ref
        obs.create_dataset("n_counts", data=np.arange(n_obs, dtype="int64"))

        var = f.create_group("var")
        _encoded(var, "dataframe")
        var.attrs["_index"] = "_index"
        var.attrs["column-order"] = np.asarray(("gene_symbol",), dtype=object)
        var.create_dataset("_index", data=["ENSG00000000"], dtype=VLEN)
        var.create_dataset("gene_symbol", data=["GENE0"], dtype=VLEN)
    return path


def write_h5ad_categories_unordered(path: Path) -> Path:
    """The same store, with no ``column-order`` to hide ``__categories``.

    Without it the column names come from the group's keys, and ``__categories``
    is one of them — a group holding other columns' labels, described as a
    column of its own. anndata reserves the name, so excluding it drops nothing.
    """
    write_h5ad_categories_group(path)
    with h5py.File(path, "a") as f:
        del f["obs"].attrs["column-order"]
    return path


def write_null_dataspace(path: Path) -> Path:
    """A dataset that declares no dataspace at all, beside ordinary ones.

    Legal HDF5, and what PyTables writes for an empty ``TITLE``. h5py reports
    its shape as ``None``, which used to cost the file its whole description.
    """
    with h5py.File(path, "w") as f:
        f.create_dataset("empty", data=h5py.Empty("f8"))
        f.create_dataset("real", data=np.arange(3, dtype="int64"))
        f.create_group("g").create_dataset("also_empty", data=h5py.Empty("i4"))
    return path


# ---------------------------------------------------------------------------
# 10x Genomics feature-barcode matrices
# ---------------------------------------------------------------------------


def _csc(group, n_rows: int, n_cols: int, *, dtype="int32", shape_child=True, pt=False):
    """A CSC matrix with one column per barcode.

    ``indptr`` has one entry per column plus a terminator, so its length pins
    which axis ``shape`` names — the invariant an earlier synthetic fixture
    broke, leaving the axis convention unsettled. ``indices`` is ``int64`` and
    ``shape`` ``int32``, as in every real file dumped.
    """
    per_column = 2
    nnz = n_cols * per_column
    made = {
        "data": group.create_dataset("data", data=np.ones(nnz, dtype=dtype)),
        "indices": group.create_dataset(
            "indices", data=(np.arange(nnz) % n_rows).astype("int64")
        ),
        "indptr": group.create_dataset(
            "indptr", data=np.arange(n_cols + 1, dtype="int64") * per_column
        ),
    }
    if shape_child:
        made["shape"] = group.create_dataset(
            "shape", data=np.asarray([n_rows, n_cols], dtype="int32")
        )
    if pt:
        for dataset in made.values():
            _pytables(dataset, "CARRAY", titled=True)


def write_tenx(path: Path, n_features: int = 30, n_barcodes: int = 200) -> Path:
    """Cell Ranger v3 and later: one ``matrix`` group, features in a subgroup.

    The root attribute set of a real file, and fixed-length strings throughout.
    """
    with h5py.File(path, "w") as f:
        f.attrs["chemistry_description"] = "Single Cell 3' v3"
        f.attrs["filetype"] = "matrix"
        f.attrs["library_ids"] = _fixed(["probe_library"])
        f.attrs["original_gem_groups"] = np.asarray([1], dtype="int64")
        f.attrs["version"] = np.int64(2)

        matrix = f.create_group("matrix")
        matrix.create_dataset(
            "barcodes", data=_fixed([f"BC{i:06d}-1" for i in range(n_barcodes)])
        )
        _csc(matrix, n_features, n_barcodes)

        features = matrix.create_group("features")
        features.create_dataset("_all_tag_keys", data=_fixed(["genome"]))
        for name, values in (
            ("id", [f"ENSG{i:08d}" for i in range(n_features)]),
            ("name", [f"GENE{i}" for i in range(n_features)]),
            ("feature_type", ["Gene Expression"] * n_features),
            ("genome", ["GRCh38"] * n_features),
        ):
            features.create_dataset(name, data=_fixed(values))
    return path


def write_tenx_headless(path: Path, n_barcodes: int = 20) -> Path:
    """A ``matrix`` group with no ``features`` beneath it."""
    with h5py.File(path, "w") as f:
        f.attrs["filetype"] = "matrix"
        matrix = f.create_group("matrix")
        matrix.create_dataset(
            "barcodes", data=[f"BC{i:06d}-1" for i in range(n_barcodes)], dtype=VLEN
        )
        _csc(matrix, 5, n_barcodes)
    return path


def write_tenx_featureless_matrix(path: Path, n_features: int = 5) -> Path:
    """Features and no matrix: the mirror of :func:`write_tenx_headless`."""
    with h5py.File(path, "w") as f:
        f.attrs["filetype"] = "matrix"
        features = f.create_group("matrix/features")
        features.create_dataset(
            "id", data=[f"ENSG{i:08d}" for i in range(n_features)], dtype=VLEN
        )
    return path


def write_tenx_dataset_features(path: Path, n_features: int = 5) -> Path:
    """``matrix/features`` as a *dataset* rather than a group.

    A group is what holds a feature table; a dataset of the same name has no
    children to make columns from, so recognising it would give a features
    table carrying only the counts array.
    """
    with h5py.File(path, "w") as f:
        f.attrs["filetype"] = "matrix"
        matrix = f.create_group("matrix")
        matrix.create_dataset(
            "features", data=[f"ENSG{i:08d}" for i in range(n_features)], dtype=VLEN
        )
        _csc(matrix, n_features, 4)
    return path


def write_fat_attributes(path: Path, megabytes: int = 8) -> Path:
    """A one-byte dataset behind megabytes of root attributes.

    Attributes hold values, and a Keras ``model_config`` or a MATLAB header is
    this shape in miniature.
    """
    with h5py.File(path, "w") as f:
        f.attrs["encoding-type"] = "not-a-layout-this-reader-knows"
        for i in range(megabytes):
            f.attrs[f"blob{i}"] = "x" * 1_000_000
        f["tiny"] = np.arange(1, dtype="int8")
    return path


def _legacy_genome(f, genome: str, n_genes: int, n_barcodes: int):
    """One Cell Ranger 2 genome group, as PyTables wrote it."""
    group = f.create_group(genome)
    _pytables(group, "GROUP", "1.0")
    for name, values in (
        ("barcodes", [f"BC{i:06d}-1" for i in range(n_barcodes)]),
        ("gene_names", [f"GENE{i}" for i in range(n_genes)]),
        ("genes", [f"ENSG{i:08d}" for i in range(n_genes)]),
    ):
        _pytables(
            group.create_dataset(name, data=_fixed(values)), "CARRAY", titled=True
        )
    _csc(group, n_genes, n_barcodes, pt=True)
    return group


def write_tenx_legacy(
    path: Path, n_genes: int = 30, n_barcodes: int = 200, genome: str = "GRCh38"
) -> Path:
    """Cell Ranger v2: one group named for the genome.

    It carries ``filetype = matrix`` like every real one, which is the whole
    reason this fixture was rewritten: the reader used to refuse on that
    attribute's presence, so the legacy layout matched nothing real.
    """
    with h5py.File(path, "w") as f:
        f.attrs["CLASS"] = np.bytes_(b"GROUP")
        f.attrs["FILTERS"] = np.int64(65793)
        f.attrs["PYTABLES_FORMAT_VERSION"] = np.bytes_(b"2.1")
        f.attrs["TITLE"] = np.bytes_(b"")
        f.attrs["VERSION"] = np.bytes_(b"1.0")
        f.attrs["chemistry_description"] = np.bytes_(b"Single Cell 3' v2")
        f.attrs["filetype"] = np.bytes_(b"matrix")
        f.attrs["library_ids"] = _fixed(["legacy_library"])
        f.attrs["original_gem_groups"] = np.asarray([1], dtype="int64")
        _legacy_genome(f, genome, n_genes, n_barcodes)
    return path


def write_tenx_legacy_widthless(
    path: Path, indptr=None, genome: str = "GRCh38"
) -> Path:
    """Cell Ranger v2's five names over an ``indptr`` that gives no width.

    Every name recognition looks for is present and none of them says how many
    barcodes there are. ``indptr`` defaults to a scalar, which declares no
    length at all; pass a sequence for a length that declares no columns.
    """
    with h5py.File(path, "w") as f:
        f.attrs["filetype"] = np.bytes_(b"matrix")
        group = f.create_group(genome)
        group.create_dataset("barcodes", data=_fixed(["BC000000-1"]))
        group.create_dataset("gene_names", data=_fixed(["GENE0"]))
        group.create_dataset("genes", data=_fixed(["ENSG00000000"]))
        group.create_dataset("data", data=np.ones(1, dtype="int32"))
        group.create_dataset(
            "indptr",
            data=np.int64(2) if indptr is None else np.asarray(indptr, dtype="int64"),
        )
    return path


def write_tenx_foreign_filetype(path: Path) -> Path:
    """Cell Ranger v2's shape under a ``filetype`` naming something else.

    A container that says what it is is described by what it says. ``matrix``
    is what a real legacy file declares and is accepted; anything else is a
    different format wearing a familiar shape.
    """
    write_tenx_legacy(path)
    with h5py.File(path, "a") as f:
        f.attrs["filetype"] = np.bytes_(b"molecule_info")
    return path


def write_tenx_barnyard(path: Path, n_genes: int = 30, n_barcodes: int = 200) -> Path:
    """Two genome groups, as a barnyard run wrote them.

    Both are wholly present, so both are described. scanpy and DropletUtils
    refuse this file because their readers return exactly one matrix; a
    manifest is under no such constraint.
    """
    write_tenx_legacy(path, n_genes, n_barcodes, genome="hg19")
    with h5py.File(path, "a") as f:
        _legacy_genome(f, "mm10", n_genes, n_barcodes)
    return path


def write_tenx_legacy_with_a_sibling(path: Path) -> Path:
    """One genome group beside a group that is not one.

    Real files carry extra top-level groups, and requiring exactly one group at
    the root sent them all to the generic view.
    """
    write_tenx_legacy(path)
    with h5py.File(path, "a") as f:
        f.create_group("metadata").create_dataset("note", data=_fixed(["run 1"]))
    return path


# ---------------------------------------------------------------------------
# HDF5 from outside single-cell, which is what the generic path is for
# ---------------------------------------------------------------------------


def write_keras(path: Path) -> Path:
    """A Keras-style model file: a JSON config attribute over weight tensors."""
    with h5py.File(path, "w") as f:
        f.attrs["model_config"] = '{"class_name": "Sequential"}'
        f.attrs["keras_version"] = "2.15.0"
        weights = f.create_group("model_weights")
        for layer, shape in (("dense", (784, 128)), ("dense_1", (128, 10))):
            group = weights.create_group(layer)
            group.create_dataset("kernel:0", shape=shape, dtype="float32")
            group.create_dataset("bias:0", shape=(shape[1],), dtype="float32")
    return path


def write_netcdf(path: Path) -> Path:
    """A NetCDF4-style file: ``_NCProperties`` over dimension-scale variables."""
    with h5py.File(path, "w") as f:
        f.attrs["_NCProperties"] = "version=2,netcdf=4.9.2,hdf5=1.14.3"
        f.create_dataset("time", data=np.arange(12, dtype="float64"))
        f.create_dataset("lat", data=np.linspace(-90, 90, 5, dtype="float32"))
        f.create_dataset("lon", data=np.linspace(-180, 180, 7, dtype="float32"))
        f.create_dataset("tas", shape=(12, 5, 7), dtype="float32")
    return path


def write_matlab(path: Path) -> Path:
    """A MATLAB v7.3-style file: the superblock sits behind a 512-byte header."""
    with h5py.File(path, "w", userblock_size=512) as f:
        for name, data in (
            ("counts", np.arange(6, dtype="float64").reshape(2, 3)),
            ("label", np.frombuffer("probe".encode("utf-16-le"), dtype="uint16")),
        ):
            dataset = f.create_dataset(name, data=data)
            dataset.attrs["MATLAB_class"] = "double" if name == "counts" else "char"
    with open(path, "r+b") as fh:
        fh.write(b"MATLAB 7.3 MAT-file, Platform: probe, Created by: croissant-baker")
    return path


def write_plain(path: Path) -> Path:
    """Three datasets at three depths, and nothing that hints at a layout."""
    with h5py.File(path, "w") as f:
        f.create_dataset("top", data=np.arange(4, dtype="int16"))
        f.create_dataset("group/middle", data=np.zeros((2, 3), dtype="uint8"))
        f.create_dataset("group/deeper/bottom", shape=(5, 6, 7), dtype="float64")
    return path


def write_dense(path: Path, megabytes: int = 8) -> Path:
    """A file matching no layout whose datasets carry real, allocated bytes.

    The generic view describes every one of them, so a reader that read what it
    describes would pull the whole file through.
    """
    with h5py.File(path, "w") as f:
        for i in range(megabytes):
            f.create_dataset(f"block{i:02d}", data=np.ones(125_000, dtype="float64"))
    return path


def write_dtypes(path: Path) -> Path:
    """One dataset per dtype the mapping has a row for, plus the traps."""
    with h5py.File(path, "w") as f:
        f["ints/i8"] = np.arange(2, dtype="int8")
        f["ints/i64"] = np.arange(2, dtype="int64")
        f["ints/u16"] = np.arange(2, dtype="uint16")
        f["floats/f16"] = np.arange(2, dtype="float16")
        f["floats/f64"] = np.arange(2, dtype="float64")
        f["flag"] = np.array([True, False])
        f["scalar"] = np.int64(7)
        f.create_dataset("text/vlen", data=["a", "bb"], dtype=VLEN)
        f.create_dataset("text/fixed", data=np.array([b"ab", b"cd"], dtype="S4"))
        # kind == 'O' for both of the next two. Only h5py's dtype checks part them.
        f.create_dataset("opaque/refs", (2,), dtype=h5py.ref_dtype)
        f.create_dataset(
            "opaque/ragged",
            data=[np.arange(2), np.arange(3)],
            dtype=h5py.vlen_dtype(np.int32),
        )
        f["opaque/bytes"] = np.void(b"\x01\x02")
        f["table"] = np.zeros(
            3, dtype=[("index", "S8"), ("age", "i8"), ("score", "f4")]
        )
    return path


def write_links(path: Path, target: Path) -> Path:
    """Every kind of link, including the two that must never be followed."""
    with h5py.File(target, "w") as f:
        f.create_dataset("outside/secret", data=np.arange(5, dtype="int64"))
    with h5py.File(path, "w") as f:
        described = f.create_group("described")
        described.create_dataset("real", data=np.arange(3, dtype="int32"))
        described["cycle"] = h5py.SoftLink("/described")
        f["soft"] = h5py.SoftLink("/described/real")
        f["soft_broken"] = h5py.SoftLink("/absent")
        f["external"] = h5py.ExternalLink(str(target), "/outside")
        f["external_broken"] = h5py.ExternalLink("absent.h5", "/outside")
    return path


def write_many(path: Path, count: int) -> Path:
    """``count`` leaf datasets, spread over groups of fifty."""
    with h5py.File(path, "w") as f:
        for i in range(count):
            f.require_group(f"g{i // 50:03d}").create_dataset(
                f"d{i:05d}", data=np.arange(2, dtype="float32")
            )
    return path


#: Per fixture, values stored *inside* it that must never be described.
#:
#: A group or dataset *name* is not one of them. A name is part of a path, and
#: a path is structure — it is what tells a reader where a column is, and the
#: generic view is built out of nothing else. That is why ``GRCh38`` is
#: forbidden for :func:`write_tenx`, which stores it as data in
#: ``features/genome``, and not for :func:`write_tenx_legacy`, which names its
#: group for the reference and stores no genome column at all.
FORBIDDEN_VALUES = {
    write_h5ad: ("cell_0", "bc_0", "NK", "GENE0", "ENSG00000000", "lot_0"),
    write_h5ad_compound: ("louvain_categories",),
    write_h5ad_categories_group: ("bc_0", "GENE0", "ENSG00000000"),
    write_tenx: (
        "BC000000",
        "ENSG00000000",
        "GENE0",
        "GRCh38",
        "Gene Expression",
        "probe_library",
    ),
    write_tenx_legacy: ("BC000000", "ENSG00000000", "GENE0", "legacy_library"),
    write_tenx_barnyard: ("BC000000", "ENSG00000000", "GENE0", "legacy_library"),
}


def tenx_bytes(**kwargs) -> bytes:
    """The 10x sample, as bytes. h5py needs a real path, so this goes via one."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        return write_tenx(Path(tmp) / "matrix.h5", **kwargs).read_bytes()
