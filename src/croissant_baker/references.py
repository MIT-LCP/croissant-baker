"""Heuristic foreign-key detection across RecordSets (Croissant ``cr:references``).

Croissant can express a foreign key by giving a Field a ``references`` Source that
points at another RecordSet's field. croissant-baker treats every file as an
island today (see issue #51 and the inline TODO in ``metadata_generator``); this
module adds an opt-in pass that links shared key columns across RecordSets.

Design: **conservative, and it never guesses a direction.** A reference is emitted
only when a shared key column has a parent RecordSet that can be identified *by
name* — e.g. a ``study_id`` column shared by several tables, with a RecordSet
named ``studies`` (or ``study``) to act as the parent. Shared keys with no
name-identifiable parent are *reported as unresolved*, not linked: surfacing the
candidate to the user without inventing a relationship that might point the wrong
way. (A future refinement could pick the parent by column-value uniqueness — the
true primary-key signal — at the cost of reading the key columns.)

The detector is a pure function over lightweight descriptors so it is trivially
testable; ``metadata_generator`` maps its output back onto the real Field objects.
"""

import re
from collections import defaultdict
from typing import Dict, List, Tuple, TypedDict

# A key-like column is ``<stem>_id`` (subject_id, hadm_id, study_id, ...). A bare
# ``id`` is intentionally excluded: it is too generic to attribute to a parent by
# name, and almost always denotes the *local* primary key rather than a foreign
# key into another table.
_KEY_RE = re.compile(r"^(?P<stem>.+)_id$", re.IGNORECASE)


class RecordSetDescriptor(TypedDict):
    """Minimal view of a RecordSet needed for foreign-key detection."""

    id: str
    name: str
    columns: List[str]


class ForeignKeyLink(TypedDict):
    """A detected foreign key: ``child.column`` references ``parent.parent_column``."""

    child_rs: str
    column: str
    parent_rs: str
    parent_column: str


class UnresolvedKey(TypedDict):
    """A shared key column for which no parent RecordSet could be named."""

    column: str
    record_sets: List[str]


def _parent_name_variants(stem: str) -> set:
    """Return plausible parent table names for a key stem.

    ``study_id`` -> a parent table called ``study``, ``studys``, or ``studies``.
    Handles the common English pluralisations so name matching catches the usual
    ``<entity>`` / ``<entity>s`` / ``<entity>(y->ies)`` conventions.
    """
    stem = stem.lower()
    variants = {stem, stem + "s"}
    if stem.endswith("y"):
        variants.add(stem[:-1] + "ies")
    if stem.endswith(("s", "x", "z", "ch", "sh")):
        variants.add(stem + "es")
    return variants


def detect_foreign_keys(
    record_sets: List[RecordSetDescriptor],
) -> Tuple[List[ForeignKeyLink], List[UnresolvedKey]]:
    """Detect foreign-key relationships between RecordSets by column name.

    Args:
        record_sets: descriptors with ``id``, ``name`` and top-level ``columns``.

    Returns:
        ``(links, unresolved)``. ``links`` are confident foreign keys (a parent
        RecordSet was named); ``unresolved`` are shared key columns seen in two or
        more RecordSets for which no parent could be identified by name — reported
        so the caller can surface them rather than silently dropping the signal.
        Both lists are deterministically ordered.
    """
    columns_to_holders: Dict[str, List[RecordSetDescriptor]] = defaultdict(list)
    for rs in record_sets:
        for col in rs["columns"]:
            columns_to_holders[col].append(rs)

    name_index: Dict[str, RecordSetDescriptor] = {}
    for rs in record_sets:
        # First RecordSet wins a given name; collisions are already disambiguated
        # upstream, so this is just a stable lookup.
        name_index.setdefault((rs["name"] or "").lower(), rs)

    links: List[ForeignKeyLink] = []
    unresolved: List[UnresolvedKey] = []

    for col, holders in sorted(columns_to_holders.items()):
        if len(holders) < 2:
            continue  # not shared — nothing to link
        match = _KEY_RE.match(col)
        if not match:
            continue  # not key-like (no ``_id`` suffix)

        stem = match.group("stem")
        # The parent is the RecordSet named after the stem (singular/plural) that
        # also carries the key column itself. Requiring the column to be present
        # keeps v1 coherent — it links same-named shared keys (the convention
        # issue #51 describes, e.g. subject_id repeated across tables) and points
        # the reference at a real field rather than a fabricated one. (A Rails-
        # style parent whose own key is a bare ``id`` is a possible later
        # extension; for now such a key, present only in the child, is skipped.)
        parent = None
        for variant in _parent_name_variants(stem):
            candidate = name_index.get(variant)
            if candidate is not None and col in candidate["columns"]:
                parent = candidate
                break

        if parent is None:
            unresolved.append(
                {"column": col, "record_sets": [rs["id"] for rs in holders]}
            )
            continue

        for rs in holders:
            if rs["id"] == parent["id"]:
                continue  # the parent does not reference itself
            links.append(
                {
                    "child_rs": rs["id"],
                    "column": col,
                    "parent_rs": parent["id"],
                    "parent_column": col,
                }
            )

    return links, unresolved
