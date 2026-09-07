"""Every handler describes a wrapped file exactly as it describes its twin."""

import ast
import copy
import gzip
from pathlib import Path

import pytest

from croissant_baker import compression
from croissant_baker.assembly import _matches
from croissant_baker.handlers.registry import (
    get_registered_handlers,
    register_all_handlers,
    select_handler,
)
from croissant_baker.scan import Reason

from tests.helpers import (
    SAMPLES,
    WRAPPER_SUFFIXES,
    bake,
    write_all,
    write_wrapped,
)


def _handlers():
    register_all_handlers()
    return get_registered_handlers()


def _formats(metadata: dict) -> list:
    """The media types on each FileObject, in document order."""
    out = []
    for node in metadata.get("distribution", []):
        if node.get("@type") != "cr:FileObject":
            continue
        value = node.get("encodingFormat")
        out.append(value if isinstance(value, list) else [value])
    return out


def _rename_ids(node, rename: dict):
    """Replace every FileObject identifier with the name of the file it describes."""
    if isinstance(node, dict):
        return {k: _rename_ids(v, rename) for k, v in node.items()}
    if isinstance(node, list):
        return [_rename_ids(v, rename) for v in node]
    return rename.get(node, node) if isinstance(node, str) else node


def _normalise(metadata: dict) -> dict:
    """The described document, with transport differences removed and nothing else."""
    doc = copy.deepcopy(metadata)
    wrapper_types = {c.media_type for c in compression.compressions()}

    rename = {
        node["@id"]: f"file:{compression.logical_name(node['contentUrl'])}"
        for node in doc.get("distribution", [])
        if node.get("@type") == "cr:FileObject"
    }
    doc = _rename_ids(doc, rename)

    distribution = []
    for node in doc.get("distribution", []):
        formats = node.get("encodingFormat")
        formats = formats if isinstance(formats, list) else [formats]
        node["encodingFormat"] = [f for f in formats if f not in wrapper_types]
        if node.get("@type") == "cr:FileObject":
            node["name"] = compression.logical_name(node["name"])
            node["contentUrl"] = compression.logical_name(node["contentUrl"])
            node.pop("contentSize", None)
            node.pop("sha256", None)
        else:
            node["includes"] = _logical_includes(node.get("includes"))
        distribution.append(node)

    distribution.sort(key=lambda n: (n.get("@type", ""), n.get("@id", "")))
    # Record sets follow rglob order too, which differs between two freshly
    # created directories holding the same files.
    record_sets = sorted(doc.get("recordSet", []), key=lambda n: n.get("@id", ""))
    out = {"recordSet": record_sets, "distribution": distribution}
    _logicalise_descriptions(out)
    return out


def _logicalise_descriptions(node) -> None:
    """Reduce every filename inside a ``description`` to its logical form."""
    if isinstance(node, dict):
        text = node.get("description")
        if isinstance(text, str):
            node["description"] = " ".join(
                compression.logical_name(word) for word in text.split(" ")
            )
        for value in node.values():
            _logicalise_descriptions(value)
    elif isinstance(node, list):
        for item in node:
            _logicalise_descriptions(item)


def _logical_includes(includes) -> list:
    """Reduce a FileSet's includes to the form a plain dataset would emit."""
    patterns = includes if isinstance(includes, list) else [includes]
    reduced = {compression.logical_name(pattern) for pattern in patterns}
    return sorted(reduced)


@pytest.mark.parametrize("handler_name", sorted(SAMPLES))
@pytest.mark.parametrize("suffix", WRAPPER_SUFFIXES)
def test_wrapped_is_described_like_plain(
    handler_name: str, suffix: str, tmp_path: Path
) -> None:
    """The wrapper is transport: the same files described twice, once wrapped,
    must produce the same document but for the media types.

    No ``suffix=""`` column. It compared a document to a copy of itself, which
    ``_normalise`` sorts into agreement, and its media-type half reduced to
    ``_formats(plain) == _formats(plain)``. The two coverage assertions were
    the only thing it carried, so they run on the plain bake here instead.
    """
    files = SAMPLES[handler_name]()

    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    write_all(plain_dir, files, "")

    wrapped_dir = tmp_path / "wrapped"
    wrapped_dir.mkdir()
    write_all(wrapped_dir, files, suffix)

    plain, wrapped = bake(plain_dir), bake(wrapped_dir)

    assert _normalise(wrapped) == _normalise(plain), (
        f"{handler_name}: {suffix} described differently from the plain files"
    )

    # The media types are the one thing that legitimately differs, so they are
    # asserted rather than normalised away. A handler fusing the two — the
    # application/x-nifti+gzip bug — fails here even if it never writes a
    # suffix literal for the grep check to find.
    wrapper = next(c for c in compression.compressions() if c.suffix == suffix)
    expected = [formats + [wrapper.media_type] for formats in _formats(plain)]
    assert _formats(wrapped) == expected, (
        f"{handler_name}: {suffix} reported {_formats(wrapped)}"
    )

    for document, directory in ((plain, plain_dir), (wrapped, wrapped_dir)):
        _assert_every_stored_file_is_covered(document, directory)
        _assert_no_wrapper_is_claimed_without_a_file(document, directory)


def _assert_no_wrapper_is_claimed_without_a_file(
    metadata: dict, directory: Path
) -> None:
    """A FileSet may only name a wrapper its own files actually use.

    Two ways to break it, and this catches both: a compressed ``includes``
    variant matching nothing on disk, and an ``encodingFormat`` naming a
    compression no member arrived in. Deriving the wrapper set per handler
    batch rather than per FileSet produces exactly these.

    The plain patterns a handler declares are its own — ``**/*.dicom`` beside
    ``**/*.dcm`` names a second spelling of one extension, and stays whether or
    not a file uses it. Only the variants the pipeline derives are asserted.
    """
    stored = [
        str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()
    ]
    wrapper_types = {c.media_type: c.suffix for c in compression.compressions()}

    for file_set in [
        n for n in metadata["distribution"] if n.get("@type") == "cr:FileSet"
    ]:
        patterns = (
            file_set["includes"]
            if isinstance(file_set["includes"], list)
            else [file_set["includes"]]
        )
        for pattern in patterns:
            if not compression.is_compressed(pattern):
                continue
            assert any(_matches(pattern, s) for s in stored), (
                f"{file_set['@id']}: {pattern} matches no file in {stored}"
            )

        formats = file_set.get("encodingFormat")
        formats = formats if isinstance(formats, list) else [formats]
        covered = [s for s in stored if any(_matches(p, s) for p in patterns)]
        used = {compression.compression_for(Path(s).name) for s in covered}
        used = {c.media_type for c in used if c is not None}
        assert {f for f in formats if f in wrapper_types} <= used, (
            f"{file_set['@id']}: claims {formats}, members use {used or 'none'}"
        )


def _assert_every_stored_file_is_covered(metadata: dict, directory: Path) -> None:
    """A described file must be matched by an include in some FileSet."""
    file_sets = [n for n in metadata["distribution"] if n.get("@type") == "cr:FileSet"]
    if not file_sets:
        return  # this handler describes per-file, with no FileSet
    patterns = [
        p
        for fs in file_sets
        for p in (
            fs["includes"] if isinstance(fs["includes"], list) else [fs["includes"]]
        )
    ]
    for stored in sorted(directory.rglob("*")):
        if not stored.is_file():
            continue
        rel = stored.relative_to(directory)
        assert any(_matches(pattern, str(rel)) for pattern in patterns), (
            f"{rel} is described but matched by none of {patterns}"
        )


@pytest.fixture
def fauxzip():
    """A fourth compression, registered for one test. gzip bytes, new suffix."""
    extra = compression.Compression(
        "fauxzip", ".fz", "application/x-fauxzip", gzip.open
    )
    compression.register_compression(extra)
    yield extra


@pytest.mark.parametrize("handler_name", sorted(SAMPLES))
def test_a_newly_registered_compression_works_for_every_handler(
    handler_name: str, fauxzip, tmp_path: Path
) -> None:
    """No handler was edited to make this pass."""
    files = SAMPLES[handler_name]()

    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    write_all(plain_dir, files, "")

    wrapped_dir = tmp_path / "wrapped"
    wrapped_dir.mkdir()
    write_all(wrapped_dir, files, ".fz")

    assert _normalise(bake(wrapped_dir)) == _normalise(bake(plain_dir))


def test_a_newly_registered_compression_reaches_the_media_types(
    fauxzip, tmp_path: Path
) -> None:
    write_all(tmp_path, SAMPLES["CSVHandler"](), ".fz")

    entry = next(
        d for d in bake(tmp_path)["distribution"] if d.get("@type") == "cr:FileObject"
    )
    assert entry["encodingFormat"] == ["text/csv", "application/x-fauxzip"]


def test_a_compressed_wfdb_header_is_refused_with_its_own_reason(
    tmp_path: Path,
) -> None:
    write_wrapped(tmp_path, "record.hea", b"record 1 1 10\nrecord.dat 16 200\n", ".gz")

    selection = select_handler(tmp_path / "record.hea.gz")

    assert selection.handler is None
    assert "gzip" in selection.refusal
    assert "on disk" in selection.refusal
    assert selection.refusal != "no handler for this file type"


def test_a_plain_wfdb_header_is_still_claimed(tmp_path: Path) -> None:
    (tmp_path / "record.hea").write_text("record 1 1 10\nrecord.dat 16 200\n")
    assert type(select_handler(tmp_path / "record.hea").handler).__name__ == (
        "WFDBHandler"
    )


@pytest.mark.parametrize(
    "name",
    [
        "bundle.zip",
        "bundle.tar",
        "bundle.tgz",
        "bundle.tar.gz",
        "bundle.tar.bz2",
        "bundle.tar.xz",
    ],
)
def test_every_archive_spelling_is_recognised(name: str) -> None:
    assert compression.is_archive(name)


def test_an_archive_composes_with_a_runtime_compression(fauxzip) -> None:
    """A new compression must not need adding to the archive list as well."""
    assert compression.is_archive("bundle.tar.fz")


@pytest.mark.parametrize("name", ["bundle.zip", "bundle.tgz", "bundle.tar.xz"])
def test_an_archive_is_refused_with_the_archive_reason(name: str, tmp_path) -> None:
    (tmp_path / name).write_bytes(b"PK\x03\x04 not really")

    selection = select_handler(tmp_path / name)

    assert selection.handler is None
    assert selection.reason is Reason.ARCHIVE


def _executable_strings(path: Path):
    """Every string literal in a module except the docstrings."""
    tree = ast.parse(path.read_text())
    docs = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
        and isinstance(n.body[0].value.value, str)
    }
    return [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in docs
    ]


def test_no_handler_branches_on_a_wrapper_suffix_or_media_type() -> None:
    """The one boundary check behaviour cannot make. A handler is handed the
    logical name, so a ``.gz`` branch never fires — it is dead code that
    teaches the next reader the wrong contract, and no bake can notice."""
    literals = {c.suffix for c in compression.compressions()}
    literals |= {c.media_type for c in compression.compressions()}
    handlers = (
        Path(__file__).parent.parent / "src" / "croissant_baker" / "handlers"
    ).glob("*_handler.py")

    offenders = [
        f"{path.name}:{lineno}: {value!r}"
        for path in sorted(handlers)
        for lineno, value in _executable_strings(path)
        for literal in literals
        if literal in value
    ]
    assert not offenders, offenders
