"""The vocabulary of the scan stage: outcomes, reasons, diagnostics, entries.

A leaf module. It imports nothing from the package, so any module that needs to
name an outcome or a reason can import it without pulling in the pipeline —
:mod:`croissant_baker.duplicates` and :mod:`croissant_baker.report` both do.

Entries are mutated in place by the extraction stage, one thread per entry, and
read back in scan order, so output does not depend on how many workers ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover - handlers import this module at runtime
    from croissant_baker.handlers.base_handler import FileTypeHandler


class Outcome(str, Enum):
    """What became of a file the scan found.

    ``str`` mixin so an outcome serialises to its own value in the JSON report.
    ``PENDING``, ``READY`` and ``WOULD_PROCESS`` are working states; a completed
    bake leaves none of them behind.
    """

    #: Not yet resolved. Every entry starts here.
    PENDING = "pending"
    #: A handler claimed the file and extraction succeeded. Its
    #: ``build_croissant`` has still to run.
    READY = "ready"
    #: A handler described the file and its structure was assembled.
    DESCRIBED = "described"
    #: The file is another described file in a different form. Its bytes are
    #: still described; its structure is not.
    LINKED = "linked"
    #: Nothing claimed the file on its own, and the document carries it anyway:
    #: a handler reading a multi-file record emitted a FileObject for it. A
    #: WFDB header brings its ``.dat`` and ``.atr`` in this way.
    REFERENCED = "referenced"
    #: No handler took the file. See :class:`Reason` for which way.
    UNCLAIMED = "unclaimed"
    #: The file was claimed and then lost at claim, extraction or assembly time.
    FAILED = "failed"
    #: ``--dry-run`` only: a handler claimed the file and nothing was read.
    WOULD_PROCESS = "would_process"


class Reason(str, Enum):
    """Why a file was not described, as one of a finite set of categories.

    The entry's ``detail`` names the file and the exception; this is what the
    summary counts, so terminal output stays bounded by the number of *kinds*
    of problem.
    """

    #: Nothing claimed the file.
    NO_HANDLER = "no_handler"
    #: A multi-member archive. The baker reports archives and does not open them.
    ARCHIVE = "archive"
    #: A handler recognised the format but needs an uncompressed file on disk.
    UNSUPPORTED_INPUT = "unsupported_input"
    #: Deciding who owned the file raised — usually a corrupt wrapper.
    CLAIM_FAILED = "claim_failed"
    #: The handler took the file and failed to read it.
    EXTRACT_FAILED = "extract_failed"
    #: The handler read the file and failed to assemble its Croissant nodes.
    BUILD_FAILED = "build_failed"
    #: A plain file and its wrapper share a logical name. Linked on the naming
    #: convention alone; contents were not compared.
    DUPLICATE_BY_NAME = "duplicate_by_name"
    #: Two candidates decompressed to the same bounded prefix.
    PROBABLE_DUPLICATE = "probable_duplicate"
    #: Named like a partition of a table whose other shards disagree on schema.
    PARTITION_SCHEMA_CONFLICT = "partition_schema_conflict"


#: One short label per reason, for the fixed-size terminal summary.
REASON_LABELS: Dict[Reason, str] = {
    Reason.NO_HANDLER: "no handler",
    Reason.ARCHIVE: "archive, not opened",
    Reason.UNSUPPORTED_INPUT: "handler needs an uncompressed file on disk",
    Reason.CLAIM_FAILED: "unreadable while selecting a handler",
    Reason.EXTRACT_FAILED: "extraction failed",
    Reason.BUILD_FAILED: "could not be assembled",
    Reason.DUPLICATE_BY_NAME: "duplicate by naming convention",
    Reason.PROBABLE_DUPLICATE: "probable duplicate of another file",
    Reason.PARTITION_SCHEMA_CONFLICT: "partition schema conflict",
}


class DiagnosticCode(str, Enum):
    """Why part of a file the document carries was not described.

    The other half of :class:`Reason`, which says why a *file* is not. A
    diagnostic changes no outcome and no coverage total.
    """

    #: A part of the file could not be described. A workbook's sheet is one.
    SHEET_SKIPPED = "sheet_skipped"
    #: An optional part of the container was malformed and was ignored.
    PROPERTIES_IGNORED = "properties_ignored"


#: One short label per code, for the fixed-size terminal summary.
DIAGNOSTIC_LABELS: Dict[DiagnosticCode, str] = {
    DiagnosticCode.SHEET_SKIPPED: "sheet not described",
    DiagnosticCode.PROPERTIES_IGNORED: "malformed optional properties ignored",
}


@dataclass(frozen=True)
class Diagnostic:
    """One thing a handler could not do to a file it otherwise described.

    Attributes:
        code: The category a caller branches on and the summary counts.
        detail: The sentence for a human.
        part: The part it applies to — a sheet name, for a workbook — or
            ``None`` when it is about the file as a whole.
    """

    code: DiagnosticCode
    detail: str
    part: Optional[str] = None


@dataclass(eq=False)
class ScanEntry:
    """One file the scan found, and what became of it.

    The transition methods enforce the lifecycle rather than overwriting:

    .. code-block:: text

        PENDING -> READY -> DESCRIBED     handler read it, nodes assembled
                         -> LINKED        a duplicate of a described file
                                -> FAILED the file it duplicates was not described
                         -> FAILED        assembly raised
                -> UNCLAIMED              nothing took it
                       -> REFERENCED      another file's handler described it
                -> FAILED                 claim or extraction raised
                -> WOULD_PROCESS          --dry-run stops here

    ``DESCRIBED``, ``LINKED`` and ``REFERENCED`` are the three ways into the
    document. Everything else terminal is a file the document does not carry.

    Attributes:
        path: Path relative to the dataset root, wrapper suffix included — the
            file *as stored*. It never crosses into a handler; see
            :mod:`croissant_baker.sources` for the logical view handlers get.
        reason: Which category of problem applied, for every outcome other than
            ``DESCRIBED``.
        detail: The human-readable explanation behind ``reason``.
        meta: The metadata dict the handler produced.
        duplicate_of: The entry this file duplicates. Set for ``LINKED``.
        part_of: The entry whose record carries this file. Set for
            ``REFERENCED``.
        diagnostics: What a handler could not describe about a file it did
            describe. Outlives every transition.

    Compared by identity, so entries can key the generator's staging dicts.
    """

    path: Path
    outcome: Outcome = Outcome.PENDING
    reason: Optional[Reason] = None
    detail: str = ""
    handler: Optional["FileTypeHandler"] = None
    meta: Optional[dict] = None
    duplicate_of: Optional["ScanEntry"] = None
    part_of: Optional["ScanEntry"] = None
    diagnostics: List[Diagnostic] = field(default_factory=list)

    @property
    def name(self) -> str:
        """The file's basename, wrapper suffix included."""
        return self.path.name

    def _move(self, to: Outcome, *allowed_from: Outcome) -> None:
        if self.outcome not in allowed_from:
            raise ValueError(
                f"{self.path}: cannot move from {self.outcome.value} to "
                f"{to.value}; expected one of "
                f"{', '.join(o.value for o in allowed_from)}"
            )
        self.outcome = to

    def ready(self, handler: "FileTypeHandler", meta: dict) -> None:
        """Record that ``handler`` read this file. Its nodes are not built yet.

        Diagnostics move onto the entry because ``meta`` is dropped if a later
        stage fails the file, and what the handler could not read is still so.
        """
        self._move(Outcome.READY, Outcome.PENDING)
        self.handler = handler
        self.meta = meta
        self.diagnostics = list(meta.get("diagnostics") or ())

    def describe(self) -> None:
        """Record that this file's Croissant nodes were assembled.

        ``reason`` and ``detail`` go; ``diagnostics`` stay, because a
        described workbook with an unreadable sheet is what they are for.
        """
        self._move(Outcome.DESCRIBED, Outcome.READY)
        self.reason = None
        self.detail = ""

    def unclaimed(self, reason: Reason, detail: str) -> None:
        """Record that no handler took this file."""
        self._move(Outcome.UNCLAIMED, Outcome.PENDING)
        self.reason = reason
        self.detail = detail

    def would_process(self, handler: "FileTypeHandler") -> None:
        """Record that a handler claimed this file, without reading it."""
        self._move(Outcome.WOULD_PROCESS, Outcome.PENDING)
        self.handler = handler

    def failed(self, reason: Reason, error: BaseException) -> None:
        """Record that this file was lost, at whichever stage ``reason`` names.

        A linked duplicate can be lost too: its structure was its primary's, so
        it goes when the primary's does.

        ``meta`` goes, since nothing downstream may read a partial
        description; ``diagnostics`` record what reading it found, and stay.
        """
        self._move(Outcome.FAILED, Outcome.PENDING, Outcome.READY, Outcome.LINKED)
        self.reason = reason
        self.detail = str(error) or type(error).__name__
        self.meta = None

    def referenced(self, parent: "ScanEntry") -> None:
        """Record that ``parent``'s handler put this file in the document.

        Reachable from ``FAILED`` as well as ``PENDING`` and ``UNCLAIMED``:
        whether a file could be read on its own is a different question from
        whether the document carries it, and a handler reading a multi-file
        record answers the second one. What its own failure said is kept in
        ``detail``, since nothing else records that reading it was attempted.

        The reason goes either way. A reason says why the document does not
        carry a file, and this one is in it.
        """
        failure = self.detail if self.outcome is Outcome.FAILED else ""
        self._move(
            Outcome.REFERENCED, Outcome.PENDING, Outcome.UNCLAIMED, Outcome.FAILED
        )
        self.part_of = parent
        self.reason = None
        self.detail = f"described as part of {parent.path}"
        if failure:
            self.detail += f"; reading it on its own failed: {failure}"

    def linked(self, primary: "ScanEntry", reason: Reason, detail: str) -> None:
        """Record that this file duplicates ``primary``.

        The entry keeps its own distribution entry — its bytes, size and
        checksum are its own — and gives up only its structure.
        """
        self._move(Outcome.LINKED, Outcome.READY)
        self.duplicate_of = primary
        self.reason = reason
        self.detail = detail
