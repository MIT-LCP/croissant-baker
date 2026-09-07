"""Deciding when two files in one directory describe the same data.

Split from the scan stage because it answers a different question. The scan
walks a directory and records what became of each file; this reads bytes and
compares them. The evidence each rule is entitled to — and what it must never
claim — is documented in ``docs/user-guide/supported-formats.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional

from croissant_baker import compression
from croissant_baker.entries import Outcome, Reason, ScanEntry


#: How much decompressed data a duplicate check reads from each candidate.
PREFIX_BYTES = 64 * 1024


def resolve_duplicates(entries: List[ScanEntry], root: Path) -> None:
    """Collapse files that describe the same data onto a single description.

    Two files with one logical path would emit two record sets under one @id,
    which is not a Croissant document. The shapes and the evidence each needs
    are documented in ``docs/user-guide/supported-formats.md``.

    Args:
        entries: Scan entries after extraction. Only ``READY`` ones take part,
            so a file whose duplicate failed to extract is still described.
            Mutated in place.
        root: The dataset directory, for reading the prefixes.
    """
    groups: Dict[tuple, List[ScanEntry]] = {}
    for entry in entries:
        if entry.outcome is not Outcome.READY:
            continue
        logical = compression.logical_name(entry.name)
        # Directory plus stem, so every candidate lands in one group and is
        # decided against a single primary.
        key = (str(entry.path.parent), Path(logical).stem)
        groups.setdefault(key, []).append(entry)

    for members in groups.values():
        if len(members) > 1:
            _resolve_group(members, root)


def _resolve_group(members: List[ScanEntry], root: Path) -> None:
    """Link whichever members of one candidate group turn out to duplicate."""
    primary = choose_primary(members)
    for entry in members:
        if entry is primary:
            continue
        verdict = _compare(primary, entry, root)
        if verdict is not None:
            entry.linked(primary, *verdict)


def _compare(primary: ScanEntry, other: ScanEntry, root: Path) -> Optional[tuple]:
    """Decide whether ``other`` duplicates ``primary``, and on what evidence.

    Compressed sizes are never evidence: gzip and xz sizes are not comparable,
    and two files that differ can compress to the same length.
    """
    primary_logical = compression.logical_name(primary.name)
    other_logical = compression.logical_name(other.name)

    if primary_logical == other_logical:
        # One of the pair is the plain file the other wraps. Linked on the
        # naming convention alone: verifying it would cost a full decompression
        # pass, and the reason string says the content was not compared.
        if compression.is_compressed(primary.name) != compression.is_compressed(
            other.name
        ):
            return (
                Reason.DUPLICATE_BY_NAME,
                f"same logical name as {primary.path}; linked by naming "
                "convention, content not verified",
            )
    elif not compression.is_compressed(primary.name) and not compression.is_compressed(
        other.name
    ):
        # Two plain files of different size cannot match.
        if _stored_size(root / primary.path) != _stored_size(root / other.path):
            return None

    left = _read_prefix(root / primary.path)
    right = _read_prefix(root / other.path)
    if left is None or right is None or left != right:
        return None
    return (
        Reason.PROBABLE_DUPLICATE,
        f"first {len(left)} decompressed bytes are identical to "
        f"{primary.path}; linked as a probable duplicate",
    )


def _stored_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return -1


def _read_prefix(path: Path) -> Optional[bytes]:
    """Up to :data:`PREFIX_BYTES` decompressed bytes, or ``None`` if unreadable."""
    try:
        with compression.open_binary(path) as stream:
            return stream.read(PREFIX_BYTES)
    except (OSError, EOFError, ValueError):
        return None


def choose_primary(members: Iterable[ScanEntry]) -> ScanEntry:
    """Pick the member of a duplicate group that keeps its structure.

    Uncompressed first, then registration order of the compression, then the
    stored path lexically, so the choice does not depend on ``rglob`` order.
    """
    order = {c.suffix: i for i, c in enumerate(compression.compressions())}

    def rank(entry: ScanEntry) -> tuple:
        wrapper = compression.compression_for(entry.name)
        return (
            wrapper is not None,
            order.get(wrapper.suffix, len(order)) if wrapper else -1,
            str(entry.path),
        )

    return min(members, key=rank)
