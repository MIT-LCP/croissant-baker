"""Keeping every ``@id`` in one document distinct.

JSON-LD merges nodes that share an identifier, so a collision silently loses
data rather than failing. Each handler names identifiers inside its own batch
and cannot see the others', so collisions are only visible once every batch is
built — which is here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from croissant_baker.handlers.utils import sanitize_id


def _handler_label(handler) -> str:
    """A short, stable discriminator for a handler, for identifier collisions."""
    name = type(handler).__name__
    if name.endswith("Handler"):
        name = name[: -len("Handler")]
    return sanitize_id(name).lower() or "handler"


def _rename_record_set(record_set, new_id: str) -> None:
    """Point a record set and every field beneath it at a new identifier.

    Field identifiers are ``{record_set}/{column}``, and sub-fields extend that
    with another segment, so every identifier in the subtree carries the record
    set's own as a prefix. Only the prefix moves; the column names, which are
    what a reader matches on, are untouched.
    """
    old_prefix = f"{record_set.id}/"
    record_set.id = new_id

    def rewrite(fields) -> None:
        for f in fields or []:
            if f.id and f.id.startswith(old_prefix):
                f.id = f"{new_id}/{f.id[len(old_prefix) :]}"
            rewrite(getattr(f, "sub_fields", None))

    rewrite(record_set.fields)


def _disambiguate_record_sets(batches: list) -> list:
    """Give same-stem record sets from different handlers distinct identifiers.

    ``sample.csv`` and ``sample.tsv`` both shorten to the stem ``sample``, and
    each handler names identifiers within its own batch, so the collision is
    only visible once every batch has been built. Every member of a colliding
    group is suffixed, so the outcome does not depend on which handler ran
    first: ``sample_csv`` and ``sample_tsv``, never ``sample`` and
    ``sample_tsv``.

    Args:
        batches: ``(handler, record_sets)`` pairs, one per handler that
            contributed. Colliding record sets are renamed in place.

    Returns:
        Every record set, in batch order.
    """
    by_id: dict = defaultdict(list)
    for handler, record_sets in batches:
        for record_set in record_sets:
            by_id[record_set.id].append((handler, record_set))

    taken = {rs_id for rs_id, members in by_id.items() if len(members) == 1}
    for rs_id, members in by_id.items():
        if len(members) == 1:
            continue
        for handler, record_set in members:
            candidate = f"{rs_id}_{_handler_label(handler)}"
            # A file actually named sample_csv could already hold it.
            if candidate in taken:
                n = 2
                while f"{candidate}__{n}" in taken:
                    n += 1
                candidate = f"{candidate}__{n}"
            taken.add(candidate)
            _rename_record_set(record_set, candidate)

    return [rs for _, record_sets in batches for rs in record_sets]


def serialize_datetime(obj):
    """Convert datetime objects to ISO format strings for JSON serialization."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _assert_unique_node_ids(distributions: list, record_sets: list) -> None:
    """Verify every emitted @id is unique across the document.

    JSON-LD merges nodes that share an @id (`json-ld11/#node-identifiers`
    spec section: nodes with the same identifier represent the same node).
    A collision therefore silently merges nodes, producing incorrect
    Croissant output. Surfacing the conflict here keeps the failure
    local to the generator with the offending @id and node types
    attached, instead of leaking out as an opaque downstream validation
    error or, worse, passing validation while silently dropping data.
    """
    seen: dict = {}

    def _claim(node_id, kind: str) -> None:
        if node_id is None:
            return
        if node_id in seen:
            raise ValueError(
                f"Croissant @id collision: '{node_id}' is used by both "
                f"{seen[node_id]} and {kind}. Every FileObject, FileSet, "
                f"RecordSet, and Field must carry a unique @id."
            )
        seen[node_id] = kind

    def _walk_fields(fields) -> None:
        for f in fields or []:
            _claim(getattr(f, "id", None), "Field")
            _walk_fields(getattr(f, "sub_fields", None))

    for d in distributions:
        _claim(getattr(d, "id", None), type(d).__name__)
    for r in record_sets:
        _claim(getattr(r, "id", None), "RecordSet")
        _walk_fields(getattr(r, "fields", None))
