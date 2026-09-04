"""HDF5 handler: turning what a container holds into Croissant.

One record set per file, or one per table where
:mod:`croissant_baker.handlers.layouts` recognised the layout. The reading is
elsewhere: :mod:`croissant_baker.handlers.hdf5` knows HDF5, ``layouts`` knows
what a container holds and no storage format, and this module knows neither.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import mlcroissant as mlc

from croissant_baker.handlers import hdf5, layouts
from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.utils import (
    display_name,
    make_field_id,
    make_record_set_ids,
)
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

#: Unregistered, and ``application/x-hdf`` is also in the wild. No media type
#: makes these record sets readable by mlcroissant, so this is documentation.
MIME_TYPE = "application/x-hdf5"

#: What a field falls back to when nothing in the container types it.
UNTYPED = "sc:Text"


class HDF5Handler(FileTypeHandler):
    """Handler for HDF5 containers (``.h5``, ``.h5ad``, ``.hdf5``).

    Claims a declared extension whose bytes carry the HDF5 signature — which
    may sit behind a user block rather than at offset 0, so the extension alone
    does not settle it.
    """

    EXTENSIONS = (".h5", ".h5ad", ".hdf5")
    FORMAT_NAME = "HDF5"
    FORMAT_DESCRIPTION = (
        "Dataset paths, dtypes and shapes; AnnData and 10x table columns "
        "where the layout is recognised"
    )

    def claims(self, source: FileSource) -> bool:
        if source.suffix not in self.EXTENSIONS:
            return False
        return hdf5.looks_like_hdf5(source.peek(hdf5.PEEK_BYTES))

    def extract(self, source: FileSource, **kwargs) -> dict:
        if not source.exists:
            raise FileNotFoundError(f"HDF5 file not found: {source.relative_path}")

        try:
            with hdf5.opened(source) as root:
                layout = layouts.recognise(root)
                structure = None if layout else layouts.structure(root)
        except Exception as e:
            raise ValueError(
                f"Failed to read HDF5 file {source.relative_path}: {e}"
            ) from e

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": MIME_TYPE,
            "layout": layout,
            "structure": structure,
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """One record set per table, or one per file for an unrecognised one.

        No FileSet: one HDF5 file is one container, and a FileSet spanning
        several would claim they share a schema.
        """
        if not file_metas:
            return BuildResult([], [])

        bases = make_record_set_ids(file_metas)
        # A bare base is claimed before any suffixed identifier is derived, so
        # ``sample.h5ad`` cannot take ``sample_obs`` from a file actually named
        # ``sample_obs.h5``. Claiming them in batch order instead would make
        # the outcome depend on which file the scan reached first.
        taken = {
            base for base, meta in zip(bases, file_metas) if meta.get("layout") is None
        }
        record_sets: List[mlc.RecordSet] = []
        for base, meta, file_id in zip(bases, file_metas, file_ids):
            record_sets.extend(_record_sets(base, taken, meta, file_id))
        return BuildResult([], record_sets)


def _allocate(candidate: str, taken: set) -> str:
    """``candidate``, suffixed until it is unique document-wide."""
    chosen, n = candidate, 2
    while chosen in taken:
        chosen = f"{candidate}__{n}"
        n += 1
    taken.add(chosen)
    return chosen


def _record_sets(base: str, taken: set, meta: dict, file_id: str) -> list:
    layout: Optional[layouts.Layout] = meta.get("layout")
    stored = display_name(meta)
    if layout is None:
        # ``base`` is this file's, reserved above and unique among the bases.
        return [_structure_record_set(base, meta["structure"], stored, file_id)]
    return [
        _table_record_set(
            _allocate(f"{base}_{table.key}", taken),
            layout,
            table,
            stored,
            file_id,
            note=index == 0,
        )
        for index, table in enumerate(layout.tables)
    ]


# ---------------------------------------------------------------------------
# The generic record set
# ---------------------------------------------------------------------------


def _structure_record_set(
    rs_id: str, structure: layouts.Structure, stored: str, file_id: str
) -> mlc.RecordSet:
    return mlc.RecordSet(
        id=rs_id,
        name=rs_id,
        description=_structure_description(structure, stored),
        fields=_fields(rs_id, structure.columns, file_id, stored, None),
    )


def _structure_description(structure: layouts.Structure, stored: str) -> str:
    parts = [
        f"HDF5 structure of {stored} — {len(structure.columns)} dataset(s) in "
        f"{structure.groups} group(s), each field named by its HDF5 path. "
        "One record, the file itself: the datasets share no row axis."
    ]
    if structure.capped:
        # No count. How many were left behind was not measured, because
        # measuring it means the walk the cap exists to avoid, and a manifest
        # that states a number it did not measure is worse than one that says
        # it does not know.
        parts.append(
            f"The cap of {layouts.MAX_DATASETS} datasets was reached, so at "
            "least one further dataset is not described here."
        )
    parts.append(_links_sentence(structure))
    return " ".join(part for part in parts if part)


def _links_sentence(structure: layouts.Structure) -> str:
    """What was reachable and not described, so nothing goes missing in
    silence. An external link's target is never named: it is a path outside
    the dataset."""
    clauses = []
    if structure.external:
        clauses.append(
            f"{structure.external} external link(s) were not followed, so "
            "whatever they point at is not described here"
        )
    if structure.broken:
        clauses.append(f"{structure.broken} broken link(s) had no target")
    return f"{'; '.join(clauses)}." if clauses else ""


# ---------------------------------------------------------------------------
# A recognised layout's tables
# ---------------------------------------------------------------------------


def _table_record_set(
    rs_id: str,
    layout: layouts.Layout,
    table: layouts.Table,
    stored: str,
    file_id: str,
    *,
    note: bool,
) -> mlc.RecordSet:
    rows = "" if table.rows is None else f"{table.rows} row(s), "
    parts = [
        f"{layout.name} {table.key} table from {stored} — {rows}"
        f"one row per {table.row}, {len(table.columns)} field(s), "
        f"columns at /{table.path}."
    ]
    if note and layout.undescribed:
        parts.append(
            f"Present in the file and not described: {', '.join(layout.undescribed)}."
        )
    return mlc.RecordSet(
        id=rs_id,
        name=rs_id,
        description=" ".join(parts),
        fields=_fields(rs_id, table.columns, file_id, stored, table),
    )


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------


def _fields(
    rs_id: str,
    columns: Tuple[layouts.Column, ...],
    file_id: str,
    stored: str,
    table: Optional[layouts.Table],
) -> list:
    used: set = set()
    return [_field(rs_id, column, file_id, stored, table, used) for column in columns]


def _field(
    rs_id: str,
    column: layouts.Column,
    file_id: str,
    stored: str,
    table: Optional[layouts.Table],
    used: set,
    parent: Optional[layouts.Column] = None,
) -> mlc.Field:
    field_id = make_field_id(rs_id, column.name, used)
    members = [
        _field(field_id, member, file_id, stored, table, set(), parent=column)
        for member in column.members
    ]
    # mlcroissant rejects a field carrying neither a dataType nor sub-fields,
    # so a type the reader could not determine becomes text rather than
    # nothing. A record array's members carry the typing instead.
    data_types = None if members else [column.data_type or UNTYPED]
    return mlc.Field(
        id=field_id,
        name=column.name,
        description=_describe(column, stored, table, parent),
        data_types=data_types,
        is_array=True if column.array_shape else None,
        array_shape=column.array_shape or None,
        # No extract. mlcroissant validates one and cannot execute it: its
        # reader dispatches on encodingFormat over CSV, Parquet, JSON, image
        # and archive types, and has no HDF5 reader at any media type. The
        # path in the description is what a reader follows instead.
        source=mlc.Source(file_object=file_id),
        sub_fields=members or None,
    )


def _describe(
    column: layouts.Column,
    stored: str,
    table: Optional[layouts.Table],
    parent: Optional[layouts.Column],
) -> str:
    """Where the value is, in the container's own terms.

    Every field states its own :attr:`~croissant_baker.handlers.layouts.Column.path`,
    so a reader infers nothing from a naming convention. The stored name comes
    last and bare, because that is the file it can be found in on disk.
    """
    note = f" ({column.note})" if column.note else ""
    if parent is not None:
        return (
            f"Member '{column.name}' of the record array /{parent.path} "
            f"in {stored}{note}"
        )
    if table is None:
        return f"HDF5 dataset /{column.path} in {stored}{note}"
    if column.array_shape:
        return (
            f"Array /{column.path}, indexed by the {table.key} axis, in {stored}{note}"
        )
    return f"Column '{column.name}' at /{column.path} in {stored}{note}"
