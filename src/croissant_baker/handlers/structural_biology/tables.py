"""Record sets for the tables a CIF or STAR document holds.

Two handlers meet the same grammar: a STAR file is one document of blocks and
loops, and so is a CIF that turns out to carry no structure. What a block
becomes is therefore decided once, here, rather than twice with two answers.

:mod:`croissant_baker.handlers.structural_biology.cif` says what a document
holds; this module says what Croissant makes of it. A handler extracts
:class:`~croissant_baker.handlers.structural_biology.cif.Table` objects under
:data:`TABLES` and calls :func:`record_sets` with its batch.
"""

from __future__ import annotations

import logging
from typing import List

import mlcroissant as mlc

from croissant_baker.handlers.utils import (
    allocate_record_set_ids,
    display_name,
    make_field_id,
    sanitize_id,
)

logger = logging.getLogger(__name__)

#: The key a handler's extracted metadata carries its tables under.
TABLES = "tables"

#: Said on every record set: the fields carry no ``extract``, and a consumer
#: reading one in isolation cannot otherwise tell that they hold no data.
NO_VALUE_NOTICE = "No value is emitted."


def _plural(count: int, noun: str) -> str:
    """``1 row``, ``3 rows``."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def record_sets(file_metas: list, file_ids: list) -> List[mlc.RecordSet]:
    """One record set per table, over a whole batch of documents.

    Args:
        file_metas: Extracted metadata, each carrying its tables under
            :data:`TABLES`.
        file_ids: The FileObject identifier of each file, parallel to
            ``file_metas``.

    Returns:
        The record sets, in file order and then in document order.
    """
    # Each file needs its own subset of suffixes, so the batch's union is
    # allocated for all of them; a reserved id nothing emits changes no other
    # id, because every candidate is prefixed by its own file's base. Sorted,
    # because allocation order decides which candidate is moved.
    suffixes = sorted(
        {sanitize_id(table.name) for meta in file_metas for table in meta[TABLES]}
    )
    allocated = allocate_record_set_ids(file_metas, suffixes)

    out: List[mlc.RecordSet] = []
    for meta, file_id, ids in zip(file_metas, file_ids, allocated):
        out.extend(_for_file(meta, file_id, ids))
    return out


def _for_file(meta: dict, file_id: str, ids: dict) -> list:
    shown = display_name(meta)
    out = []
    for table in meta[TABLES]:
        # A block naming no column at all. mlcroissant validates a record set
        # with no field, which is why this has to refuse.
        if not table.columns:
            logger.warning(
                "%s: data block '%s' declares no column; not described",
                shown,
                table.block,
            )
            continue
        out.append(_record_set(ids[sanitize_id(table.name)], file_id, table, shown))
    return out


def _record_set(rs_id: str, file_id: str, table, shown: str) -> mlc.RecordSet:
    """One record set per table: one row per loop row, fields by column."""
    used: set = set()
    fields = [
        mlc.Field(
            id=make_field_id(rs_id, name, used),
            name=name,
            description=f"Column '{name}'",
            data_types=[data_type],
            source=mlc.Source(file_object=file_id),
        )
        for name, data_type in table.columns
    ]
    return mlc.RecordSet(
        id=rs_id,
        name=rs_id,
        description=(
            f"Data block '{table.block}' of {shown} "
            f"({_plural(table.rows, 'row')}, "
            f"{_plural(len(table.columns), 'column')}). {NO_VALUE_NOTICE}"
        ),
        fields=fields,
    )
