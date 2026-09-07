"""One contract, asserted over every built-in handler."""

from __future__ import annotations

from pathlib import Path

import pytest

from croissant_baker import compression
from croissant_baker.handlers.base_handler import (
    BuildResult,
    Declined,
    FileTypeHandler,
    InputKind,
    uses_legacy_claims,
    uses_legacy_extract,
)
from croissant_baker.handlers.registry import builtin_handlers, select_handler
from croissant_baker.sources import make_source

from tests.helpers import EXEMPT, SAMPLES, WRAPPER_SUFFIXES, write_wrapped

HANDLERS = {type(h).__name__: h for h in builtin_handlers()}
NAMES = sorted(HANDLERS)
SAMPLED = sorted(SAMPLES)

#: Extensions only one handler declares. ``.json`` is claimed by both FHIR and
#: JSON, so it cannot serve as a negative case for either.
EXCLUSIVE = {
    ext: names[0]
    for ext, names in (
        (
            ext,
            [n for n in NAMES if ext in HANDLERS[n].EXTENSIONS],
        )
        for ext in {e for h in HANDLERS.values() for e in h.EXTENSIONS}
    )
    if len(names) == 1
}


def source_for(handler: FileTypeHandler, path: Path, relative: Path | None = None):
    """The source the pipeline would hand this handler for ``path``."""
    return make_source(path, relative, with_path=handler.INPUT_KIND is InputKind.PATH)


def probe_name(name: str) -> str:
    """A filename carrying this handler's own extension."""
    if name in SAMPLES:
        return SAMPLES[name]()[0][0]
    return f"probe{HANDLERS[name].EXTENSIONS[0]}"


def test_every_handler_has_a_sample_or_a_recorded_exemption() -> None:
    """Adding a handler without test data fails here, not silently in the wild."""
    missing = [n for n in NAMES if n not in SAMPLES and n not in EXEMPT]
    assert not missing, (
        f"handlers with no sample: {missing}. Add one to tests.helpers.SAMPLES, "
        "or record in EXEMPT why it cannot take a stream."
    )


def test_only_non_stream_handlers_are_exempt() -> None:
    """An exemption has to be earned by the handler's declared input."""
    for name in EXEMPT:
        assert HANDLERS[name].INPUT_KIND is not InputKind.STREAM, (
            f"{name} reads a stream, so it has no excuse for an exemption"
        )


@pytest.mark.parametrize("suffix", ["", *WRAPPER_SUFFIXES])
@pytest.mark.parametrize("name", SAMPLED)
def test_a_handler_claims_its_own_format_wrapped_or_not(
    name: str, suffix: str, dataset: Path
) -> None:
    handler = HANDLERS[name]
    for logical, payload in SAMPLES[name]():
        path = write_wrapped(dataset, logical, payload, suffix)
        assert handler.claims(source_for(handler, path, Path(logical))), (
            f"{name} did not claim its own {logical}{suffix}"
        )


@pytest.mark.parametrize("suffix", ["", *WRAPPER_SUFFIXES])
@pytest.mark.parametrize("name", SAMPLED)
def test_a_handler_claims_its_own_format_in_upper_case(
    name: str, suffix: str, dataset: Path
) -> None:
    """``SCAN.DCM`` is the same file as ``scan.dcm``, wrapped or not.

    The behaviour is ``FileSource.suffix``'s, not any one handler's, so one
    case here covers all nine rather than the DICOM handler alone.
    """
    handler = HANDLERS[name]
    for logical, payload in SAMPLES[name]():
        shouted = str(Path(logical).with_suffix(Path(logical).suffix.upper()))
        path = write_wrapped(dataset, shouted, payload, suffix)
        assert handler.claims(source_for(handler, path, Path(shouted))), (
            f"{name} did not claim its own {shouted}{suffix}"
        )


@pytest.mark.parametrize("name", SAMPLED)
def test_case_never_changes_a_claim(name: str, dataset: Path) -> None:
    """Over every extension a handler declares, not only its sample's.

    ``.dicom`` is the second spelling DICOMHandler accepts and no sample
    carries; this is where ``.DICOM`` is exercised.
    """
    handler = HANDLERS[name]
    _, payload = SAMPLES[name]()[0]

    for ext in handler.EXTENSIONS:
        quiet = write_wrapped(dataset, f"probe{ext}", payload)
        shouted = write_wrapped(dataset, f"probe{ext.upper()}", payload)

        assert handler.claims(
            source_for(handler, shouted, Path(shouted.name))
        ) is handler.claims(source_for(handler, quiet, Path(quiet.name))), (
            f"{name} answers differently for probe{ext} and probe{ext.upper()}"
        )


@pytest.mark.parametrize("name", NAMES)
def test_a_handler_declines_another_handlers_exclusive_format(
    name: str, dataset: Path
) -> None:
    handler = HANDLERS[name]
    for ext, owner in EXCLUSIVE.items():
        if owner == name:
            continue
        sample = SAMPLES.get(owner)
        logical, payload = sample()[0] if sample else (f"other{ext}", b"payload")
        path = write_wrapped(dataset, logical, payload)
        assert not handler.claims(source_for(handler, path, Path(logical))), (
            f"{name} claimed {logical}, which belongs to {owner}"
        )


@pytest.mark.parametrize("name", SAMPLED)
def test_selection_routes_a_file_back_to_its_own_handler(
    name: str, dataset: Path
) -> None:
    logical, payload = SAMPLES[name]()[0]
    path = write_wrapped(dataset, logical, payload)
    selection = select_handler(path, Path(logical))
    assert type(selection.handler).__name__ == name


@pytest.mark.parametrize("name", NAMES)
def test_a_missing_file_raises_file_not_found(name: str, tmp_path: Path) -> None:
    """One contract, so the pipeline reports one reason category."""
    handler = HANDLERS[name]
    logical = probe_name(name)
    with pytest.raises(FileNotFoundError):
        handler.extract(source_for(handler, tmp_path / "gone" / logical, Path(logical)))


@pytest.mark.parametrize("name", NAMES)
def test_garbage_bytes_raise_a_value_error_naming_the_file(
    name: str, dataset: Path
) -> None:
    """The message becomes the reason detail a user reads in ``--report``."""
    handler = HANDLERS[name]
    logical = probe_name(name)
    path = write_wrapped(dataset, logical, b"\x00\xff not a real file \xfe\x00")

    with pytest.raises(ValueError) as caught:
        handler.extract(source_for(handler, path, Path(logical)))

    assert logical in str(caught.value), (
        f"{name} raised {caught.value!r}, which does not name the file"
    )


@pytest.mark.parametrize("name", NAMES)
def test_extensions_are_declared_and_carry_no_wrapper(name: str) -> None:
    handler = HANDLERS[name]
    assert handler.EXTENSIONS, f"{name} declares no EXTENSIONS"
    assert handler.FORMAT_NAME, f"{name} declares no FORMAT_NAME"
    for ext in handler.EXTENSIONS:
        assert ext.startswith("."), f"{name}.EXTENSIONS has {ext!r}"
        assert compression.compression_for(ext) is None, (
            f"{name}.EXTENSIONS contains the compound suffix {ext!r}; "
            "compression is stripped before a handler is asked"
        )


@pytest.mark.parametrize("name", NAMES)
def test_no_builtin_is_still_on_the_legacy_contract(name: str) -> None:
    handler = HANDLERS[name]
    assert not uses_legacy_claims(handler), f"{name} still implements can_handle"
    assert not uses_legacy_extract(handler), f"{name} still implements extract_metadata"


@pytest.mark.parametrize("name", NAMES)
def test_an_empty_batch_describes_nothing(name: str) -> None:
    """A guard that returned a node built from no files would be worse than
    the KeyError it replaced."""
    result = HANDLERS[name].build_croissant([], [])

    assert isinstance(result, BuildResult)
    assert result.file_sets == []
    assert result.record_sets == []
    assert result.declined == ()


@pytest.mark.parametrize("name", SAMPLED)
def test_a_real_batch_returns_a_build_result(name: str, dataset: Path) -> None:
    """Not a 2-tuple from eight handlers and a 3-tuple from one. The empty
    batch above is a guard; this is the path a bake actually takes."""
    handler = HANDLERS[name]
    metas, ids = [], []
    for i, (filename, payload) in enumerate(SAMPLES[name]()):
        path = write_wrapped(dataset, filename, payload)
        meta = handler.extract(source_for(handler, path, Path(filename)))
        # The generator stamps this after extraction; build_croissant needs it.
        meta["relative_path"] = filename
        metas.append(meta)
        ids.append(f"file_{i}")

    result = handler.build_croissant(metas, ids)

    assert isinstance(result, BuildResult)
    assert result.record_sets or result.file_sets, f"{name} described nothing"
    assert all(isinstance(d, Declined) for d in result.declined)


def test_a_build_result_still_unpacks_as_a_pair() -> None:
    """Direct callers written against ``(file_sets, record_sets)`` keep working."""
    from croissant_baker.handlers.base_handler import BuildResult

    file_sets, record_sets = BuildResult(["fs"], ["rs"])

    assert (file_sets, record_sets) == (["fs"], ["rs"])


@pytest.mark.parametrize(
    "returned",
    [
        (["fs"], ["rs"]),
        (["fs"], ["rs"], ()),
    ],
)
def test_a_legacy_tuple_return_is_accepted(returned) -> None:
    """A handler written against the old contract keeps working."""
    from croissant_baker.handlers.base_handler import BuildResult

    result = BuildResult.coerce(returned, 0)

    assert result.file_sets == ["fs"] and result.record_sets == ["rs"]
    assert result.declined == ()


@pytest.mark.parametrize(
    "returned",
    [
        "not a tuple",
        ([],),
        ([], [], "valid"),
        ([], [], [(0,)]),
        ([], [], [("a", "b", "c")]),
    ],
)
def test_a_malformed_return_is_refused_by_coercion(returned) -> None:
    """One validation point, so the generator never unpacks a surprise."""
    from croissant_baker.handlers.base_handler import BuildResult

    with pytest.raises((TypeError, ValueError)):
        BuildResult.coerce(returned, 1)


def test_declined_entries_are_typed() -> None:
    from croissant_baker.handlers.base_handler import BuildResult, Declined
    from croissant_baker.scan import Reason

    result = BuildResult.coerce(([], [], [(2, Reason.BUILD_FAILED, "why")]), 3)

    assert result.declined == (Declined(2, Reason.BUILD_FAILED, "why"),)


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("croissant_baker.scan", "Outcome"),
        ("croissant_baker.scan", "Reason"),
        ("croissant_baker.scan", "ScanEntry"),
        ("croissant_baker.scan", "ScanReport"),
        ("croissant_baker.scan", "scan_directory"),
        ("croissant_baker.scan", "resolve_duplicates"),
        ("croissant_baker.scan", "choose_primary"),
        ("croissant_baker.scan", "PREFIX_BYTES"),
        ("croissant_baker.metadata_generator", "MetadataGenerator"),
        ("croissant_baker.metadata_generator", "serialize_datetime"),
        ("croissant_baker.handlers.registry", "find_handler"),
        ("croissant_baker.handlers.registry", "register_all_handlers"),
        ("croissant_baker.handlers.base_handler", "BuildResult"),
        ("croissant_baker.handlers.base_handler", "FileTypeHandler"),
    ],
)
def test_the_documented_import_paths_keep_working(module: str, name: str) -> None:
    """Downstream code — biotope among it — imports these by path. Splitting a
    module for cohesion must not move a name out from under them."""
    import importlib

    assert hasattr(importlib.import_module(module), name), f"{module}.{name}"


@pytest.mark.parametrize(
    "module",
    [
        "croissant_baker.entries",
        "croissant_baker.scan",
        "croissant_baker.duplicates",
        "croissant_baker.report",
    ],
)
def test_every_module_imports_first(module: str) -> None:
    """Each module must import on its own, in a fresh interpreter.

    An in-process check cannot see this: by the time the suite runs, something
    has already imported ``croissant_baker.scan`` and populated
    ``sys.modules``, which is exactly what hides a cycle.
    """
    import subprocess
    import sys

    done = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
    )

    assert done.returncode == 0, f"{module} does not import alone:\n{done.stderr}"
