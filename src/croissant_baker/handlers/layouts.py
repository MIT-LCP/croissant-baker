"""What a container of arrays holds: its datasets, or the tables it encodes.

Two views, and every file gets exactly one of them. :func:`recognise` returns a
:class:`Layout` for a file whose internal grammar is known — AnnData and 10x
Genomics feature-barcode matrices — where ``obs`` and ``var`` become tables
with named, typed columns. Everything else falls to :func:`structure`, which
describes each leaf dataset by its path, dtype and shape.

Nothing here is HDF5. Everything is read through :class:`Node`, which AnnData's
Zarr stores could supply just as well: anndata writes ``.zarr`` with the same
``encoding-type`` vocabulary, the same ``column-order`` and the same
shape-as-attribute as ``.h5ad``, so this knowledge is about a logical layout
rather than about a storage format. ``tests/test_layouts.py`` holds the seam to
that, with a static check that no h5py import creeps back in.

**No value from inside the file is read.** Column names, dataset paths, dtypes
and shapes only — every number that appears here is a shape or a count. A
categorical's labels in particular are not read: HDF5 guarantees nothing about
what they describe, so the handler cannot tell an assay vocabulary from a
clinical one, and emits neither.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Protocol, Sequence, Tuple

# ---------------------------------------------------------------------------
# The vocabulary a Node speaks
# ---------------------------------------------------------------------------

#: ``Node.dtype`` for a dataset whose elements are text, of any width or
#: encoding. The byte width of a fixed-length string is a stated loss.
STRING = "string"
#: ``Node.dtype`` for an object or region reference: a pointer to somewhere
#: else in the container, carrying no value of its own.
REFERENCE = "reference"
#: ``Node.dtype`` for bytes the container declines to interpret.
OPAQUE = "opaque"
#: ``Node.dtype`` for a record array. ``Node.fields`` names the members.
COMPOUND = "compound"

#: ``Node.unresolved``: a link out of the container, which is never followed.
EXTERNAL = "external link"
#: ``Node.unresolved``: a link whose target is absent.
BROKEN = "broken link"
#: ``Node.unresolved``: a link back to a node already on the path to it.
CYCLE = "cyclic link"

#: Layout names. Both come from a fixed enumeration the format itself defines —
#: AnnData's ``encoding-type`` and 10x's ``filetype`` — so naming one is not
#: reporting a value out of the data.
ANNDATA = "AnnData"
TENX = "10x feature-barcode matrix"

#: The most leaf datasets :func:`structure` describes. A simulation or physics
#: file holds thousands; describing it to here and saying so beats both
#: truncating in silence and emitting a record set the length of the file.
MAX_DATASETS = 300


class Node(Protocol):
    """One node of a container tree: a group, a dataset, or an unresolved link.

    ``dtype`` is ``None`` for anything that is not a dataset, so that is the
    test for one. The tree is finite: a link back to a node already on the path
    to it arrives as :data:`CYCLE` rather than as another level.
    """

    #: The node's own name within its parent.
    name: str
    #: Where the walk reached it, relative to the root and without a leading
    #: separator. Not the container's canonical name for the object, which is
    #: one of possibly several and would report a linked dataset under a path
    #: the reader did not ask about.
    path: str
    #: Something hashable that is equal for two paths to one object. Used to
    #: keep the tree finite; never emitted.
    identity: object
    #: Empty for a node that was read; otherwise why it was not.
    unresolved: str
    #: The normalised element type, or ``None`` for anything but a dataset.
    dtype: Optional[str]
    #: The dimensions, ``()`` for a scalar, ``None`` for anything but a
    #: dataset. A dimension whose size is not declared is ``-1``.
    shape: Optional[Tuple[int, ...]]
    #: ``(name, dtype)`` per member of a :data:`COMPOUND` dataset, else None.
    fields: Optional[Tuple[Tuple[str, str], ...]]

    def keys(self) -> Tuple[str, ...]:
        """The names of this node's children, in one order for every file."""

    def child(self, name: str) -> Optional["Node"]:
        """The named child, or ``None`` if this node has no such name."""

    def attr(self, name: str) -> Optional[object]:
        """The named attribute, decoded, or ``None``.

        One at a time, and never a mapping over all of them. Attributes carry
        *values*: a Keras ``model_config`` or a MATLAB header runs to
        megabytes, and a reader that materialised the set would have read the
        file's payload after all — measured at 8 MB against 5 KiB for the same
        file. Asking by name is what keeps the read bounded, so the interface
        does not offer the other way.

        ``None`` for an attribute that is absent, and for one the container
        cannot decode to plain Python — a reference, which names something
        rather than holding a value. Presence is therefore tested as
        ``attr(name) is not None``.
        """


# ---------------------------------------------------------------------------
# What either view produces
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """One thing that becomes a Croissant field.

    A table column, an array attached to a table's axis, or — in the generic
    view — a leaf dataset named by its container path.
    """

    name: str
    #: The Croissant type, or empty when :attr:`members` carries the typing.
    data_type: str
    #: Where to open the container to reach it, without a leading separator.
    #:
    #: Load-bearing, because no field can carry an ``extract`` and so the path
    #: is the only way to find the value. A column name does not give it:
    #: ``genes`` on a Cell Ranger 2 file is at ``hg19/genes``, ``id`` on a
    #: current one is at ``matrix/features/id``, and an array is indexed by an
    #: axis without being stored under it — ``X`` is at ``X``, not ``obs/X``.
    #: Equal to :attr:`name` in the generic view, where the name *is* the path;
    #: for a record array's member, the record array's own path.
    path: str = ""
    #: The dimensions after the row axis, comma-separated, or empty when the
    #: column holds one value per row.
    array_shape: str = ""
    #: One per member of a record array.
    members: Tuple["Column", ...] = ()
    #: What the type cannot say, for the description.
    note: str = ""


@dataclass(frozen=True)
class Table:
    """One axis of a recognised layout, with the columns indexed by it."""

    #: The suffix the record set's identifier takes: ``obs``, ``features``, …
    key: str
    #: What one row is, for the description.
    row: str
    #: The container path the columns live under. Not the arrays' — see
    #: :attr:`Column.path`.
    path: str
    columns: Tuple[Column, ...]
    #: How many rows, when a shape declares it.
    rows: Optional[int] = None


@dataclass(frozen=True)
class Layout:
    """A container whose internal grammar was recognised."""

    name: str
    tables: Tuple[Table, ...]
    #: Top-level names deliberately left out, for the description.
    undescribed: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Structure:
    """A container described by its datasets, because it encodes no tables."""

    columns: Tuple[Column, ...]
    groups: int
    #: Whether a dataset was reached that :data:`MAX_DATASETS` had no room for.
    #: Not merely that the count reached the cap: a file holding exactly that
    #: many has lost nothing. How many were left behind is not counted, because
    #: counting them means the walk the cap exists to avoid.
    capped: bool = False
    external: int = 0
    broken: int = 0


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

_TEXT = "sc:Text"

_TYPES = {
    "int8": "cr:Int8",
    "int16": "cr:Int16",
    "int32": "cr:Int32",
    "int64": "cr:Int64",
    "uint8": "cr:UInt8",
    "uint16": "cr:UInt16",
    "uint32": "cr:UInt32",
    "uint64": "cr:UInt64",
    "float16": "cr:Float16",
    "float32": "cr:Float32",
    "float64": "cr:Float64",
    "bool": "sc:Boolean",
    STRING: _TEXT,
    # A pointer, not a label. The type cannot say so, and the note does.
    REFERENCE: _TEXT,
    OPAQUE: _TEXT,
}

_NOTES = {
    REFERENCE: "an opaque object reference, not text",
    OPAQUE: "opaque bytes the container does not interpret",
}


def croissant_type(dtype: Optional[str]) -> str:
    """The Croissant type for a normalised dtype.

    A record array returns empty: its members carry the typing. A width
    Croissant has no name for still reports as a number rather than as text.
    """
    if dtype is None or dtype == COMPOUND:
        return ""
    if dtype in _TYPES:
        return _TYPES[dtype]
    if dtype.startswith("float"):
        return "sc:Float"
    if dtype.startswith(("int", "uint")):
        return "sc:Integer"
    return _TEXT


def _shape_text(dimensions: Sequence[int]) -> str:
    return ",".join(str(int(dimension)) for dimension in dimensions)


def _leaf_column(name: str, node: Node) -> Column:
    """A dataset as one field, carrying its full shape."""
    shape = node.shape or ()
    if node.dtype == COMPOUND:
        members = tuple(
            Column(
                field_name,
                croissant_type(field_type),
                path=node.path,
                note=_NOTES.get(field_type, ""),
            )
            for field_name, field_type in (node.fields or ())
        )
    else:
        members = ()
    return Column(
        name=name,
        data_type=croissant_type(node.dtype),
        path=node.path,
        array_shape=_shape_text(shape) if shape else "",
        members=members,
        note=_NOTES.get(node.dtype or "", ""),
    )


# ---------------------------------------------------------------------------
# The generic view
# ---------------------------------------------------------------------------


def structure(root: Node, cap: int = MAX_DATASETS) -> Structure:
    """Describe every leaf dataset of ``root`` by its path, dtype and shape.

    The result has one record — the container itself — because a bag of
    unrelated datasets shares no row axis.
    """
    columns: list = []
    counts = {"groups": 0, EXTERNAL: 0, BROKEN: 0}

    def walk(node: Node) -> bool:
        """Describe ``node``'s children. False once one had to be left out."""
        for name in node.keys():
            child = node.child(name)
            if child is None:
                continue
            if child.unresolved:
                if child.unresolved in counts:
                    counts[child.unresolved] += 1
                continue
            if child.dtype is None:
                counts["groups"] += 1
                if not walk(child):
                    return False
            elif len(columns) >= cap:
                # Only here is the omission a fact rather than a possibility.
                # Stopping at the count instead would report an omission for a
                # file holding exactly the cap, which has lost nothing.
                return False
            else:
                columns.append(_leaf_column(child.path, child))
        return True

    complete = walk(root)
    return Structure(
        columns=tuple(columns),
        groups=counts["groups"],
        capped=not complete,
        external=counts[EXTERNAL],
        broken=counts[BROKEN],
    )


# ---------------------------------------------------------------------------
# Recognising a layout
# ---------------------------------------------------------------------------

_ENCODING = "encoding-type"
_INDEX = "_index"
_COLUMN_ORDER = "column-order"
_SPARSE = ("csr_matrix", "csc_matrix")

#: Names AnnData's own reader treats as the row index rather than a column.
_INDEX_NAMES = (_INDEX, "index")

#: 10x documents its feature columns in this order; HDF5 would return them
#: alphabetically, which is not how anyone reads a feature table.
_TENX_FEATURES = ("id", "name", "feature_type", "genome")
#: What a legacy 10x group must hold, all five, before it is one.
_TENX_LEGACY = ("barcodes", "data", "gene_names", "genes", "indptr")


def recognise(root: Node) -> Optional[Layout]:
    """The layout ``root`` encodes, or ``None`` for a container that encodes
    none. First match wins, and every test is over names and attributes only.
    """
    for detect in (_anndata, _tenx, _tenx_legacy):
        layout = detect(root)
        if layout is not None:
            return layout
    return None


# --- AnnData ---------------------------------------------------------------


def _anndata(root: Node) -> Optional[Layout]:
    """AnnData, current or pre-spec.

    ``encoding-type: anndata`` is written unconditionally since the on-disk
    spec. Before it there were no root attributes at all, and anndata's own
    reader still reads those files — a missing ``encoding-type`` is its empty
    spec rather than an error — so ``obs`` and ``var`` together are enough.
    """
    names = root.keys()
    if root.attr(_ENCODING) != "anndata":
        if root.attr(_ENCODING) is not None or not {"obs", "var"} <= set(names):
            return None

    uns = root.child("uns")
    tables = []
    for key, row, arrays in (
        ("obs", "observation", ("X", "layers", "obsm")),
        ("var", "variable", ("varm",)),
    ):
        node = root.child(key)
        if node is None or node.unresolved:
            continue
        columns = _dataframe_columns(node, uns)
        columns += _axis_arrays(root, arrays)
        tables.append(Table(key, row, node.path, tuple(columns), _rows(node)))

    if not tables:
        return None
    consumed = {"obs", "var", "X", "layers", "obsm", "varm"}
    return Layout(ANNDATA, tuple(tables), tuple(sorted(set(names) - consumed)))


def _rows(node: Node) -> Optional[int]:
    """How many rows a dataframe node has, from a shape and never a value."""
    if node.shape:
        return int(node.shape[0])
    index = node.child(str(node.attr(_INDEX) or _INDEX))
    if index is None:
        return None
    if index.shape:
        return int(index.shape[0])
    values = index.child("values")
    return int(values.shape[0]) if values is not None and values.shape else None


def _dataframe_columns(node: Node, uns: Optional[Node]) -> list:
    """The columns of an ``obs`` or ``var``, in the order they were authored."""
    if node.dtype is not None:
        # Pre-spec AnnData stored the whole frame as one record array: no
        # column-order, no _index, the member names are the columns, and every
        # one of them is at the record array's own path.
        return [
            Column(name, _legacy_type(name, dtype, uns), path=node.path)
            for name, dtype in (node.fields or ())
            if name not in _INDEX_NAMES
        ]

    order = node.attr(_COLUMN_ORDER)
    if isinstance(order, (list, tuple)):
        # Authoritative: anndata's reader iterates this and never the group's
        # key order, which the container returns alphabetically.
        names: Iterable[str] = [str(name) for name in order]
    else:
        index = str(node.attr(_INDEX) or _INDEX)
        names = [name for name in node.keys() if name not in (index, *_INDEX_NAMES)]

    columns = []
    for name in names:
        child = node.child(name)
        if child is None or child.unresolved:
            continue
        columns.append(Column(name, _column_type(name, child, uns), path=child.path))
    return columns


def _column_type(name: str, node: Node, uns: Optional[Node]) -> str:
    """The column's type, decided by its encoding and not by what carries it."""
    encoding = node.attr(_ENCODING)
    if encoding == "categorical":
        # The codes' width is int8 below 127 categories and int32 at 40 000: it
        # is the encoding, never the type. The group has no dtype of its own.
        return _child_type(node, "categories")
    if encoding in ("nullable-integer", "nullable-boolean"):
        return _child_type(node, "values")
    if encoding in ("string-array", "nullable-string-array", "awkward-array"):
        return _TEXT
    if encoding in _SPARSE:
        return _child_type(node, "data")
    # A group with no encoding at all is a shape no writer is known to
    # produce, and there is nothing in it to type from. Its dtype is None,
    # which the field builder turns into text rather than into nothing.
    return _legacy_type(name, node.dtype, uns)


def _legacy_type(name: str, dtype: Optional[str], uns: Optional[Node]) -> str:
    """A dtype, unless ``uns`` holds the labels an integer column stands for.

    Pre-spec AnnData wrote a categorical as integer codes beside
    ``uns/<column>_categories``. anndata still applies the rule on read, so a
    handler that ignores it describes most of what is archived as integers.
    """
    if uns is not None and dtype is not None and dtype.startswith(("int", "uint")):
        labels = uns.child(f"{name}_categories")
        if labels is not None:
            return croissant_type(labels.dtype)
    return croissant_type(dtype)


def _child_type(node: Node, name: str) -> str:
    child = node.child(name)
    return croissant_type(child.dtype) if child is not None else _TEXT


def _axis_arrays(root: Node, groups: Sequence[str]) -> list:
    """The arrays indexed by one axis: ``X`` itself, and each mapping's members.

    Only these. ``obsp`` and ``varp`` are graphs over one axis rather than
    per-row features, and ``raw`` holds a second copy of ``X`` and ``var``, so
    describing either would double every field count.
    """
    columns = []
    for group in groups:
        node = root.child(group)
        if node is None or node.unresolved:
            continue
        if node.dtype is not None or _array_shape(node) is not None:
            columns.append(_array_column(group, node))
            continue
        for name in node.keys():
            child = node.child(name)
            if child is not None and not child.unresolved:
                columns.append(_array_column(f"{group}/{name}", child))
    return columns


def _array_shape(node: Node) -> Optional[Tuple[int, ...]]:
    """A dense array's own shape, or a sparse group's shape attribute."""
    if node.shape is not None:
        return node.shape
    declared = node.attr("shape")
    if isinstance(declared, (list, tuple)):
        return tuple(int(dimension) for dimension in declared)
    return None


def _array_column(name: str, node: Node) -> Column:
    """One array, as a field of the table whose axis indexes its leading one."""
    shape = _array_shape(node) or ()
    dtype = node.dtype if node.dtype is not None else _sparse_dtype(node)
    return Column(
        name=name,
        data_type=croissant_type(dtype),
        path=node.path,
        array_shape=_shape_text(shape[1:]) if len(shape) > 1 else "",
    )


def _sparse_dtype(node: Node) -> Optional[str]:
    """A sparse group's type is its non-zero values'. Croissant 1.1 has no
    sparsity concept, so that the zeros are implicit is a stated loss."""
    data = node.child("data")
    return data.dtype if data is not None else None


# --- 10x Genomics ----------------------------------------------------------


def _tenx(root: Node) -> Optional[Layout]:
    """Cell Ranger 3 and later: one ``matrix`` group with features beneath it.

    Recognised on that shape rather than on the ``filetype`` attribute. Cell
    Ranger's own reader requires ``filetype == "matrix"``, but a ``features``
    group is what makes a feature table describable, and Xenium writes the
    same shape. A ``matrix`` group without one falls to the generic view,
    which describes it by dataset path and loses nothing.
    """
    matrix = root.child("matrix")
    if matrix is None or matrix.unresolved:
        return None
    features = matrix.child("features")
    if features is None or features.unresolved or features.dtype is not None:
        return None
    n_barcodes = _barcodes_in(matrix)
    if n_barcodes is None:
        # Without an indptr there is no matrix to attach to the features, and
        # a field standing in for one would describe something the file does
        # not hold. The generic view names whatever it does hold instead.
        return None

    n_features = _axis_length(features.child("id"))
    columns = [
        Column(name, croissant_type(child.dtype), path=child.path)
        for name, child in _feature_columns(features, n_features)
    ]
    columns.append(_matrix_column(matrix, n_barcodes))
    barcodes = matrix.child("barcodes")

    tables = [Table("features", "feature", features.path, tuple(columns), n_features)]
    if barcodes is not None and not barcodes.unresolved:
        tables.append(
            Table(
                "barcodes",
                "barcode",
                matrix.path,
                (
                    Column(
                        "barcodes",
                        croissant_type(barcodes.dtype),
                        path=barcodes.path,
                    ),
                ),
                _axis_length(barcodes),
            )
        )
    return Layout(TENX, tuple(tables), tuple(sorted(set(root.keys()) - {"matrix"})))


def _feature_columns(features: Node, n_features: Optional[int]) -> list:
    """The per-feature datasets, documented ones first.

    Filtered on the leading dimension, which is what keeps ``_all_tag_keys``
    out: it names the optional columns rather than being one of them, and its
    length is the number of tags rather than of features.
    """
    found = {}
    for name in features.keys():
        child = features.child(name)
        if child is None or child.unresolved or child.dtype is None:
            continue
        if n_features is not None and _axis_length(child) != n_features:
            continue
        found[name] = child
    ordered = [(name, found.pop(name)) for name in _TENX_FEATURES if name in found]
    return ordered + sorted(found.items())


def _barcodes_in(group: Node) -> Optional[int]:
    """How many barcodes a CSC group holds, or None if it does not say.

    10x writes one column per barcode plus a terminator, so ``indptr``'s
    declared length is the count. Taking it from a shape is what keeps this
    reader from ever loading a value: ``shape`` is a child dataset here rather
    than an attribute, unlike AnnData.

    Each detector asks before recognising anything, so a file that declares no
    width is a feature matrix this reader cannot describe as one and falls to
    the generic view.
    """
    length = _axis_length(group.child("indptr"))
    return None if length is None or length < 1 else length - 1


def _matrix_column(group: Node, barcodes: int) -> Column:
    """The counts, as one array per row of the table indexed by its rows.

    An empty ``array_shape`` means one value per row and nothing else, which is
    why the width is established before the layout rather than here.
    """
    data = group.child("data")
    return Column(
        name="matrix",
        data_type=croissant_type(data.dtype) if data is not None else _TEXT,
        path=group.path,
        array_shape=str(barcodes),
    )


def _tenx_legacy(root: Node) -> Optional[Layout]:
    """Cell Ranger 2: one group, named for the genome, holding all five names.

    Exactly one, so a barnyard run's two-genome file falls to the generic view
    rather than being read as one of its genomes. And no ``filetype``, because
    Cell Ranger 2 wrote none: a container that says what it is is described by
    what it says or not at all, never guessed at from a shape it also matches.
    """
    if root.attr("filetype") is not None:
        return None
    groups = [root.child(name) for name in root.keys()]
    groups = [
        g for g in groups if g is not None and not g.unresolved and g.dtype is None
    ]
    if len(groups) != 1:
        return None
    genome = groups[0]
    if not set(_TENX_LEGACY) <= set(genome.keys()):
        return None
    n_barcodes = _barcodes_in(genome)
    if n_barcodes is None:
        return None

    columns = []
    for name in ("genes", "gene_names"):
        child = genome.child(name)
        if child is not None:
            columns.append(Column(name, croissant_type(child.dtype), path=child.path))
    columns.append(_matrix_column(genome, n_barcodes))
    barcodes = genome.child("barcodes")

    return Layout(
        TENX,
        (
            Table(
                "genes",
                "gene",
                genome.path,
                tuple(columns),
                _axis_length(genome.child("genes")),
            ),
            Table(
                "barcodes",
                "barcode",
                genome.path,
                (
                    Column(
                        "barcodes",
                        croissant_type(barcodes.dtype),
                        path=barcodes.path,
                    ),
                ),
                _axis_length(barcodes),
            ),
        ),
        tuple(sorted(set(root.keys()) - {genome.name})),
    )


def _axis_length(node: Optional[Node]) -> Optional[int]:
    """A one-dimensional dataset's length, or None if it has no declared one."""
    if node is None or not node.shape:
        return None
    length = int(node.shape[0])
    return None if length < 0 else length
