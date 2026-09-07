"""Turning what a handler described into distribution entries.

The handler names files logically and states format globs once; only the
generator knows what is on disk. Assembling ``encodingFormat`` and resolving
FileSet ``includes`` is that translation, and it is the generator's alone.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterable, List

from croissant_baker import compression


def _encoding_formats(format_media_type: str, relative_path: str) -> List[str]:
    """The media types describing one file: its format, then its wrapper.

    A gzipped CSV is ``["text/csv", "application/gzip"]``. Croissant 1.1 gives
    ``sc:encodingFormat`` cardinality MANY, and this shape is proposal 2 of
    mlcommons/croissant#635, which is still open on how single-file compression
    should be expressed. The alternatives are worse: ``+``-concatenation
    (proposal 1) produces a string no consumer matches, and ``containedIn`` is
    the spec's mechanism for archives, whose extraction mlcroissant gates on
    tar/zip.

    Order matters. mlcroissant decides to decompress from the filename suffix,
    not from this list, and re-applies it on each iteration until a media type
    it recognises returns — so the format must come first. Reversed, a ``.gz``
    file is gunzipped twice.

    Handlers report only the format; the wrapper is the pipeline's to know.
    """
    wrapper = compression.compression_for(Path(relative_path).name)
    if wrapper is None:
        return [format_media_type]
    return [format_media_type, wrapper.media_type]


#: Characters that make an ``includes`` entry a pattern rather than a filename.
_GLOB_CHARS = ("*", "?", "[")


def _matches(pattern: str, path: str) -> bool:
    """Whether a dataset-relative ``path`` is covered by an ``includes`` pattern.

    Directory-scoped, which is what a Croissant ``includes`` means and what
    ``Path.match`` does not do: ``Path("other/data/x.parquet").match(
    "data/*.parquet")`` is ``True``, because ``match`` anchors on the right.
    Here ``*`` and ``?`` stay inside one path segment, and only ``**`` crosses
    ``/`` — matching zero or more segments, so ``**/*.png`` covers ``a.png`` as
    well as ``deep/a.png``.

    Case-insensitive, because handler dispatch is: ``FileSource.suffix``
    lowercases, so ``pixel.PNG`` is described by the same handler that declares
    ``**/*.png`` and belongs to the FileSet that pattern stands for.
    """
    return _match_segments(pattern.lower().split("/"), path.lower().split("/"))


def _match_segments(pattern: List[str], parts: List[str]) -> bool:
    if not pattern:
        return not parts
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_match_segments(rest, parts[i:]) for i in range(len(parts) + 1))
    if not parts:
        return False
    return fnmatch.fnmatchcase(parts[0], head) and _match_segments(rest, parts[1:])


def _wrappers_among(stored: Iterable[str]) -> List:
    """The compressions used by ``stored``, in registry order.

    Registry order rather than set order keeps the output stable.
    """
    present = {
        comp.suffix
        for name in stored
        if (comp := compression.compression_for(Path(name).name))
    }
    return [c for c in compression.compressions() if c.suffix in present]


def _resolve_file_sets(file_sets: list, stored_paths: dict, entries: list) -> list:
    """Point each FileSet at the files as stored, and at the wrappers they use.

    A handler names its files logically; only the generator knows what is on
    disk. An exact name becomes the stored path or paths it stands for, and a
    pattern gains one variant per compression used by the files *that pattern*
    matches. Expanding an exact name as if it were a pattern would append a
    second suffix. An exact name is matched exactly, not case-insensitively as
    a pattern is: it comes from a handler's own metadata, so it already carries
    the spelling the file is stored under.

    Each FileSet is resolved against its own members, never against the batch:
    one handler describes every file of its format in the dataset, so a wrapper
    anywhere would otherwise reach a FileSet whose own directory holds none —
    claiming ``application/gzip`` and an include matching no file.

    Membership follows the files, not the glob text. An entry belongs to a
    FileSet when a pattern matches the name it is stored under, or when it
    duplicates an entry that belongs: a twin stored as ``pixel.jpeg.gz`` rides
    with the ``pixel.jpg`` it links to, and no glob the handler declares spells
    ``.jpeg``.

    Args:
        file_sets: FileSets from one handler batch. Mutated in place.
        stored_paths: Logical dataset-relative path to the stored paths sharing
            it. A plain file and its wrapper share one logical key.
        entries: The scan entries this batch describes, including the linked
            duplicates riding along with them.

    Returns:
        ``file_sets``, for chaining.
    """
    by_logical: dict = {}
    for entry in entries:
        logical = str(entry.path.with_name(compression.logical_name(entry.path.name)))
        by_logical.setdefault(logical, []).append(entry)

    for file_set in file_sets:
        includes = getattr(file_set, "includes", None) or []
        resolved: List[str] = []
        members: List[str] = []

        for pattern in includes:
            if any(char in pattern for char in _GLOB_CHARS):
                matched = [
                    str(entry.path)
                    for logical, found in by_logical.items()
                    if _matches(pattern, logical)
                    for entry in found
                ]
                resolved.extend(
                    compression.expand_globs([pattern], _wrappers_among(matched))
                )
            else:
                matched = list(stored_paths.get(pattern, ()))
                resolved.extend(matched)
            members.extend(matched)

        for entry in _dependants_of(members, entries):
            resolved.append(str(entry.path))
            members.append(str(entry.path))

        formats = list(file_set.encoding_formats or [])
        file_set.encoding_formats = formats + [
            c.media_type
            for c in _wrappers_among(members)
            if c.media_type not in formats
        ]
        if includes:
            file_set.includes = list(dict.fromkeys(resolved))
    return file_sets


def _dependants_of(members: List[str], entries: list) -> list:
    """Entries linking to a member, that no pattern already covers.

    A duplicate gave up its structure to the file it links to, so it belongs
    wherever that file belongs. Named by its stored path, since the name it
    duplicates is by definition not the name it is stored under.
    """
    covered = set(members)
    return [
        entry
        for entry in entries
        if entry.duplicate_of is not None
        and str(entry.duplicate_of.path) in covered
        and str(entry.path) not in covered
    ]
