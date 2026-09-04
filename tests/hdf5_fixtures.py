"""HDF5 files to describe, written by hand against what the real writers emit.

Hand-built rather than produced by ``anndata`` or ``cellranger``, which are not
dependencies of this package. Every layout here was checked against the real
thing while it was written: the ``.h5ad`` is read back by anndata 0.13.3 with
all eight ``obs`` columns at their intended dtypes, and both 10x files are read
by ``scanpy.read_10x_h5`` at the expected shape. The dumps that pin the
attribute names are in the AnnData on-disk spec and the Cell Ranger H5 spec.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import h5py
import numpy as np

#: Variable-length UTF-8, which is how both writers store every string.
VLEN = h5py.string_dtype(encoding="utf-8")

#: The 10x feature table's columns, in the order Cell Ranger writes them.
TENX_FEATURE_COLUMNS = ("id", "name", "feature_type", "genome")

#: ``obs`` columns of :func:`write_h5ad`, in ``column-order``. One per encoding
#: a string column can take, plus the numeric and nullable ones.
OBS_COLUMNS = (
    "cell_id",  # all-distinct strings: anndata leaves these a string array
    "cell_type",  # low cardinality: anndata converts these to categorical
    "sex",
    "n_counts",
    "pct_mito",
    "is_doublet",
    "nullable",
    "nullable_b",
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
    group = parent.create_group(name)
    _encoded(group, "dataframe")
    group.attrs["_index"] = "_index"
    group.attrs["column-order"] = np.asarray(columns, dtype=object)
    _nullable(group, "_index", index, "nullable-string-array")
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
        _nullable(
            obs, "cell_id", [f"cell_{i}" for i in range(n_obs)], "nullable-string-array"
        )
        _categorical(obs, "cell_type", ["B", "NK", "T"], np.arange(n_obs) % 3)
        _categorical(obs, "sex", ["female", "male"], np.arange(n_obs) % 2)
        _array(obs, "n_counts", np.arange(n_obs, dtype="int64"))
        _array(obs, "pct_mito", np.linspace(0, 1, n_obs).astype("float32"))
        _array(obs, "is_doublet", np.zeros(n_obs, dtype=bool))
        _nullable(obs, "nullable", np.arange(n_obs, dtype="int64"), "nullable-integer")
        _nullable(obs, "nullable_b", np.zeros(n_obs, dtype=bool), "nullable-boolean")

        var = _dataframe(f, "var", VAR_COLUMNS, [f"ENSG{i:08d}" for i in range(n_var)])
        _nullable(
            var,
            "gene_symbol",
            [f"GENE{i}" for i in range(n_var)],
            "nullable-string-array",
        )
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


# ---------------------------------------------------------------------------
# 10x Genomics feature-barcode matrices
# ---------------------------------------------------------------------------


def _csc(group, n_rows: int, n_cols: int, *, dtype="int32", shape_child=True):
    """A CSC matrix with one column per barcode.

    ``indptr`` has one entry per column plus a terminator, so its length pins
    which axis ``shape`` names — the invariant an earlier synthetic fixture
    broke, leaving the axis convention unsettled.
    """
    per_column = 2
    nnz = n_cols * per_column
    group.create_dataset("data", data=np.ones(nnz, dtype=dtype))
    group.create_dataset("indices", data=(np.arange(nnz) % n_rows).astype("int32"))
    group.create_dataset(
        "indptr", data=np.arange(n_cols + 1, dtype="int64") * per_column
    )
    if shape_child:
        group.create_dataset("shape", data=np.asarray([n_rows, n_cols], dtype="int32"))


def write_tenx(path: Path, n_features: int = 30, n_barcodes: int = 200) -> Path:
    """Cell Ranger v3 and later: one ``matrix`` group, features in a subgroup."""
    with h5py.File(path, "w") as f:
        f.attrs["filetype"] = "matrix"
        f.attrs["version"] = 2
        f.attrs["chemistry_description"] = "Single Cell 3' v3"
        f.attrs["library_ids"] = np.asarray(["probe_library"], dtype=object)
        f.attrs["original_gem_groups"] = np.asarray([1], dtype="int64")
        f.attrs["software_version"] = "cellranger-7.1.0"

        matrix = f.create_group("matrix")
        matrix.create_dataset(
            "barcodes",
            data=[f"BC{i:06d}-1" for i in range(n_barcodes)],
            dtype=VLEN,
        )
        _csc(matrix, n_features, n_barcodes)

        features = matrix.create_group("features")
        features.create_dataset("_all_tag_keys", data=["genome"], dtype=VLEN)
        for name, values in (
            ("id", [f"ENSG{i:08d}" for i in range(n_features)]),
            ("name", [f"GENE{i}" for i in range(n_features)]),
            ("feature_type", ["Gene Expression"] * n_features),
            ("genome", ["GRCh38"] * n_features),
        ):
            features.create_dataset(name, data=values, dtype=VLEN)
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


def write_tenx_legacy(
    path: Path, n_genes: int = 30, n_barcodes: int = 200, genome: str = "GRCh38"
) -> Path:
    """Cell Ranger v2: one group named for the genome, and no ``filetype``."""
    with h5py.File(path, "w") as f:
        group = f.create_group(genome)
        group.create_dataset(
            "barcodes",
            data=[f"BC{i:06d}-1" for i in range(n_barcodes)],
            dtype=VLEN,
        )
        group.create_dataset(
            "gene_names", data=[f"GENE{i}" for i in range(n_genes)], dtype=VLEN
        )
        group.create_dataset(
            "genes", data=[f"ENSG{i:08d}" for i in range(n_genes)], dtype=VLEN
        )
        _csc(group, n_genes, n_barcodes)
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
        group = f.create_group(genome)
        group.create_dataset("barcodes", data=["BC000000-1"], dtype=VLEN)
        group.create_dataset("gene_names", data=["GENE0"], dtype=VLEN)
        group.create_dataset("genes", data=["ENSG00000000"], dtype=VLEN)
        group.create_dataset("data", data=np.ones(1, dtype="int32"))
        group.create_dataset(
            "indptr",
            data=np.int64(2) if indptr is None else np.asarray(indptr, dtype="int64"),
        )
    return path


def write_tenx_legacy_with_a_filetype(path: Path) -> Path:
    """Cell Ranger v2's shape over the ``filetype`` v2 never wrote."""
    write_tenx_legacy(path)
    with h5py.File(path, "a") as f:
        f.attrs["filetype"] = "matrix"
    return path


def write_tenx_barnyard(path: Path) -> Path:
    """Two genome groups, as a barnyard run wrote them: reading it as either
    one would silently drop the other."""
    write_tenx_legacy(path, genome="hg19")
    second = path.with_name("mm10.h5")
    write_tenx_legacy(second, genome="mm10")
    with h5py.File(path, "a") as target, h5py.File(second, "r") as extra:
        extra.copy("mm10", target)
    second.unlink()
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
    write_h5ad: ("cell_0", "bc_0", "NK", "GENE0", "ENSG00000000"),
    write_h5ad_compound: ("louvain_categories",),
    write_tenx: (
        "BC000000",
        "ENSG00000000",
        "GENE0",
        "GRCh38",
        "Gene Expression",
        "probe_library",
        "cellranger-7.1.0",
    ),
    write_tenx_legacy: ("BC000000", "ENSG00000000", "GENE0"),
}


def tenx_bytes(**kwargs) -> bytes:
    """The 10x sample, as bytes. h5py needs a real path, so this goes via one."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        return write_tenx(Path(tmp) / "matrix.h5", **kwargs).read_bytes()
