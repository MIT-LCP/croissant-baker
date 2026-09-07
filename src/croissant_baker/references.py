"""Heuristic foreign-key detection across RecordSets (Croissant ``cr:references``).

Croissant can express a foreign key by giving a Field a ``references`` Source that
points at another RecordSet's field. Before this pass, croissant-baker described
every table in isolation (see issue #51); this module adds an opt-in pass that
links shared key columns across RecordSets.

Design: **conservative, and it never guesses a direction.** A reference is emitted
only when a shared key column has a parent RecordSet that can be identified *by
name* — e.g. a ``study_id`` column shared by several tables, with a RecordSet
named ``studies`` (or ``study``) to act as the parent. Shared keys with no
name-identifiable parent are *reported as unresolved*, not linked: surfacing the
candidate to the user without inventing a relationship that might point the wrong
way. (A future refinement could pick the parent by column-value uniqueness — the
true primary-key signal — at the cost of reading the key columns.)

Two rules keep the emitted graph honest rather than merely plausible:

* A name shared by more than one RecordSet identifies nobody, so it names no
  parent. ``RecordSet.name`` carries no uniqueness guarantee — ``identifiers``
  disambiguates ``id`` and leaves ``name`` alone, so ``study.csv`` and
  ``study.tsv`` are both still named ``study`` — and electing one of them would
  make its twin a child of itself.
* A link that would close a cycle is refused. mlcroissant walks the references
  as an operation graph, and a cycle makes *every* RecordSet in the document
  unreadable while the document still validates. Columns are considered in name
  order, so the first link of a mutually-keyed pair survives and the rest are
  reported: deterministic, but alphabetical rather than a claim about the schema.

The detector is a pure function over lightweight descriptors so it is trivially
testable; ``metadata_generator`` maps its output back onto the real Field objects.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
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


class Unlinkable(str, Enum):
    """Why a shared key column was reported instead of linked.

    ``str`` mixin so a reason serialises to its own value, as
    :class:`croissant_baker.entries.Reason` does.
    """

    #: No RecordSet is named after the key's stem, or more than one is.
    NO_PARENT = "no_parent"
    #: A parent was identified, and linking to it would close a reference cycle.
    WOULD_CYCLE = "would_cycle"


UNLINKABLE_LABELS: Dict[Unlinkable, str] = {
    Unlinkable.NO_PARENT: "no single record set is named after it",
    Unlinkable.WOULD_CYCLE: "linking it would close a reference cycle",
}


class UnresolvedKey(TypedDict):
    """A shared key column that was reported rather than linked."""

    column: str
    record_sets: List[str]
    reason: Unlinkable


@dataclass(frozen=True)
class ReferenceReport:
    """What the foreign-key pass linked, and what it declined to link.

    Held by the generator and rendered by the CLI, the way
    :class:`croissant_baker.scan.ScanReport` is: the library states what it
    found, and only the CLI writes to a terminal. :meth:`summary_lines` is one
    line by default, whatever the dataset, and names a column per line only
    under ``verbose``.
    """

    links: List[ForeignKeyLink] = field(default_factory=list)
    unresolved: List[UnresolvedKey] = field(default_factory=list)

    def summary_lines(self, verbose: bool = False) -> List[str]:
        """One line saying what was linked, and under ``verbose`` what was not.

        The invitation to re-run is folded into that line rather than added as
        its own ``Tip:``, because the coverage section above may already end in
        one and two competing tips read as noise.
        """
        if not self.links and not self.unresolved:
            return ["Foreign keys: none detected."]

        line = f"Foreign keys: {len(self.links)} link(s)"
        if self.unresolved:
            line += f"; {len(self.unresolved)} shared key(s) not linked"
            if not verbose:
                line += " (re-run with --verbose to name them)"
        lines = [line + "."]

        if verbose:
            lines.extend(
                f"  {key['column']}: shared by {', '.join(key['record_sets'])}"
                f" — {UNLINKABLE_LABELS[key['reason']]}"
                for key in self.unresolved
            )
        return lines


def _parent_name_variants(stem: str) -> List[str]:
    """Plausible parent table names for a key stem, in the order they are tried.

    ``study_id`` -> a parent table called ``study``, then ``studys``, then
    ``studies``. Handles the common English pluralisations so name matching
    catches the usual ``<entity>`` / ``<entity>s`` / ``<entity>(y->ies)``
    conventions.

    Ordered, and a list rather than a set, because the order *is* the tie-break
    when a dataset holds more than one candidate: out of a set, iteration
    followed the string hashes and the chosen parent moved with
    ``PYTHONHASHSEED``. The exact stem wins, then the naive plural, then the
    grammatical one. No two variants of a stem can coincide, so the list never
    repeats itself.
    """
    stem = stem.lower()
    variants = [stem, stem + "s"]
    if stem.endswith("y"):
        variants.append(stem[:-1] + "ies")
    elif stem.endswith(("s", "x", "z", "ch", "sh")):
        variants.append(stem + "es")
    return variants


def _unique_name_index(
    record_sets: List[RecordSetDescriptor],
) -> Dict[str, RecordSetDescriptor]:
    """Look up a RecordSet by name, for the names that identify exactly one.

    A name claimed by two RecordSets identifies neither. Taking the first would
    elect a winner in filesystem discovery order and make its twin — the same
    table in another format — a foreign-key child of itself.
    """
    counts = Counter((rs["name"] or "").lower() for rs in record_sets)
    return {
        (rs["name"] or "").lower(): rs
        for rs in record_sets
        if counts[(rs["name"] or "").lower()] == 1
    }


def _reaches(parents: Dict[str, List[str]], start: str, target: str) -> bool:
    """Whether ``target`` is reachable from ``start`` by following references."""
    seen: set = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(parents.get(node, ()))
    return False


def detect_foreign_keys(
    record_sets: List[RecordSetDescriptor],
) -> Tuple[List[ForeignKeyLink], List[UnresolvedKey]]:
    """Detect foreign-key relationships between RecordSets by column name.

    Args:
        record_sets: descriptors with ``id``, ``name`` and top-level ``columns``.

    Returns:
        ``(links, unresolved)``. ``links`` are confident foreign keys (a parent
        RecordSet was named); ``unresolved`` are shared key columns seen in two
        or more RecordSets that were reported rather than linked, each carrying
        the reason — so the caller can surface them rather than silently
        dropping the signal. Both lists are deterministically ordered, and
        neither depends on the order the RecordSets arrived in.
    """
    columns_to_holders: Dict[str, List[RecordSetDescriptor]] = defaultdict(list)
    for rs in record_sets:
        # dict.fromkeys, not the raw list: a column named twice in one RecordSet
        # would otherwise let that RecordSet clear the "shared by two" gate alone.
        for col in dict.fromkeys(rs["columns"]):
            columns_to_holders[col].append(rs)

    name_index = _unique_name_index(record_sets)

    links: List[ForeignKeyLink] = []
    unresolved: List[UnresolvedKey] = []
    # child id -> the RecordSets it already references, for the cycle guard.
    parents: Dict[str, List[str]] = defaultdict(list)

    for col, holding in sorted(columns_to_holders.items()):
        if len(holding) < 2:
            continue  # not shared — nothing to link
        match = _KEY_RE.match(col)
        if not match:
            continue  # not key-like (no ``_id`` suffix)

        # Sorted by id, so neither the emitted links nor the reported record
        # sets inherit the order the scan happened to discover files in.
        holders = sorted(holding, key=lambda rs: rs["id"])

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
            unresolved.append(_unresolved(col, holders, Unlinkable.NO_PARENT))
            continue

        refused = False
        for rs in holders:
            if rs["id"] == parent["id"]:
                continue  # the parent does not reference itself
            if _reaches(parents, parent["id"], rs["id"]):
                refused = True
                continue  # would close a cycle; reported after the loop
            parents[rs["id"]].append(parent["id"])
            links.append(
                {
                    "child_rs": rs["id"],
                    "column": col,
                    "parent_rs": parent["id"],
                    "parent_column": col,
                }
            )

        if refused:
            unresolved.append(_unresolved(col, holders, Unlinkable.WOULD_CYCLE))

    return links, unresolved


def _unresolved(
    column: str, holders: List[RecordSetDescriptor], reason: Unlinkable
) -> UnresolvedKey:
    return {
        "column": column,
        "record_sets": [rs["id"] for rs in holders],
        "reason": reason,
    }
