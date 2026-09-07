"""What a completed scan is worth telling someone.

A view over resolved entries, kept apart from the entries themselves: the
terminal wants a fixed-size summary and a machine consumer wants every file,
and neither is the scan stage's business.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List

from croissant_baker.entries import REASON_LABELS, Outcome, Reason, ScanEntry

_IN_DOCUMENT = frozenset({Outcome.DESCRIBED, Outcome.LINKED, Outcome.REFERENCED})


@dataclass(frozen=True)
class ScanReport:
    """A view over resolved scan entries: what was described, and what was not.

    :meth:`summary_lines` is for the terminal, and its length depends on how
    many *kinds* of problem occurred, never on how many files did.
    :meth:`to_dict` carries the per-file detail, for ``--report`` and for
    downstream tools checking coverage.
    """

    entries: List[ScanEntry] = field(default_factory=list)

    @property
    def total(self) -> int:
        """How many files the scan found."""
        return len(self.entries)

    @property
    def described(self) -> List[ScanEntry]:
        """Entries a handler described and whose nodes were assembled."""
        return [e for e in self.entries if e.outcome is Outcome.DESCRIBED]

    @property
    def linked(self) -> List[ScanEntry]:
        """Entries carried as another described file in a different form."""
        return [e for e in self.entries if e.outcome is Outcome.LINKED]

    @property
    def referenced(self) -> List[ScanEntry]:
        """Entries another file's handler put in the document."""
        return [e for e in self.entries if e.outcome is Outcome.REFERENCED]

    @property
    def undescribed(self) -> List[ScanEntry]:
        """Entries the document does not carry, in scan order.

        Membership of the document, not of a record set: a file with a
        FileObject is in there whether it got there on its own
        (``DESCRIBED``), as another form of a described file (``LINKED``), or
        as part of a multi-file record (``REFERENCED``).
        """
        return [e for e in self.entries if e.outcome not in _IN_DOCUMENT]

    def counts(self) -> Dict[Reason, int]:
        """Number of entries per reason, in declaration order.

        Over the undescribed only: a reason answers why a file is not in the
        document, so counting one against a file that is in there says the
        opposite of what it means. A linked file keeps its own reason — that
        is the evidence it was linked on — and is not counted here.
        """
        tally = Counter(e.reason for e in self.undescribed if e.reason is not None)
        return {r: tally[r] for r in Reason if tally[r]}

    def summary_lines(self) -> List[str]:
        """A header plus at most one line per reason.

        The header names every way into the document that happened, so a run
        that linked or referenced files says so rather than folding them into
        one count. ``described`` and ``not described`` are always stated; the
        two middle buckets appear only when non-zero.
        """
        if not self.entries:
            return ["Scanned 0 files."]

        buckets = [f"{len(self.described)} described"]
        if self.linked:
            buckets.append(f"{len(self.linked)} linked")
        if self.referenced:
            buckets.append(f"{len(self.referenced)} referenced")
        buckets.append(f"{len(self.undescribed)} not described")

        lines = [f"Scanned {self.total} file(s): {', '.join(buckets)}."]
        lines.extend(
            f"  {REASON_LABELS[reason]}: {count}"
            for reason, count in self.counts().items()
        )
        return lines

    def to_dict(self) -> dict:
        """Every discovered file with its outcome.

        ``reason`` is one of a finite set a caller can branch on; ``detail`` is
        the sentence for a human. ``described``, ``linked`` and ``referenced``
        are the three ways into the document and sum with ``undescribed`` to
        ``total``; ``by_reason`` accounts for the ``undescribed`` alone.
        """
        return {
            "total": self.total,
            "described": len(self.described),
            "linked": len(self.linked),
            "referenced": len(self.referenced),
            "undescribed": len(self.undescribed),
            "by_reason": {r.value: n for r, n in self.counts().items()},
            "files": [
                {
                    "path": str(e.path),
                    "outcome": e.outcome.value,
                    **({"reason": e.reason.value} if e.reason else {}),
                    **({"detail": e.detail} if e.detail else {}),
                    **(
                        {"duplicate_of": str(e.duplicate_of.path)}
                        if e.duplicate_of
                        else {}
                    ),
                    **({"part_of": str(e.part_of.path)} if e.part_of else {}),
                }
                for e in self.entries
            ],
        }
