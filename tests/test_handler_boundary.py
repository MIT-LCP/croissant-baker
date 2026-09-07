"""A handler never observes a compression wrapper, watched from the outside."""

import gzip
import io
from pathlib import Path

import pytest
from PIL import Image

from croissant_baker.handlers.base_handler import FileTypeHandler
from croissant_baker.handlers.registry import HandlerRegistry, builtin_handlers
from croissant_baker.metadata_generator import MetadataGenerator

from tests.helpers import (
    PNG_1X1,
    SAMPLES,
    WRAPPER_SUFFIXES,
    bake,
    file_sets,
    includes,
    write_wrapped,
)


class SpyHandler(FileTypeHandler):
    """Claims ``.spy`` files and records every string it is shown."""

    EXTENSIONS = (".spy",)
    FORMAT_NAME = "Spy"
    FORMAT_DESCRIPTION = "records what the pipeline hands it"

    def __init__(self) -> None:
        self.seen: dict = {"claims": [], "extract": [], "build_croissant": []}

    def claims(self, source) -> bool:
        self.seen["claims"] += [source.name, str(source.relative_path)]
        return source.suffix == ".spy"

    def extract(self, source, **kwargs) -> dict:
        self.seen["extract"] += [source.name, str(source.relative_path)]
        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": "application/x-spy",
        }

    def build_croissant(self, file_metas, file_ids):
        for meta in file_metas:
            self.seen["build_croissant"] += [
                str(v) for v in meta.values() if isinstance(v, str)
            ]
        return [], []


def _bake_spy(tmp_path: Path, suffix: str) -> SpyHandler:
    nested = tmp_path / "sub"
    nested.mkdir()
    write_wrapped(nested, "probe.spy", b"payload", suffix)

    spy = SpyHandler()
    MetadataGenerator(
        dataset_path=str(tmp_path),
        name="spy",
        handlers=HandlerRegistry([spy, *builtin_handlers()]),
    ).generate_metadata()
    return spy


@pytest.mark.parametrize("suffix", WRAPPER_SUFFIXES)
def test_a_handler_never_decides_or_reads_through_a_wrapper(
    suffix: str, tmp_path: Path
) -> None:
    """Nothing a handler *acts on* names a wrapper."""
    spy = _bake_spy(tmp_path, suffix)

    for phase in ("claims", "extract"):
        strings = spy.seen[phase]
        assert strings, f"{phase} was never called"
        offenders = [s for s in strings if any(w in s for w in WRAPPER_SUFFIXES)]
        assert not offenders, f"{phase} was shown a wrapper: {offenders}"


@pytest.mark.parametrize("suffix", WRAPPER_SUFFIXES)
def test_build_croissant_is_given_both_names(suffix: str, tmp_path: Path) -> None:
    """Assembly gets the logical path *and* the stored name, for two jobs."""
    spy = _bake_spy(tmp_path, suffix)
    seen = spy.seen["build_croissant"]

    assert "sub/probe.spy" in seen, seen
    assert f"probe.spy{suffix}" in seen, seen


def test_the_handler_still_gets_its_directory(tmp_path: Path) -> None:
    """Logical does not mean flattened — only the wrapper suffix comes off."""
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    with gzip.open(nested / "probe.spy.gz", "wb") as fh:
        fh.write(b"payload")

    spy = SpyHandler()
    MetadataGenerator(
        dataset_path=str(tmp_path),
        name="spy",
        handlers=HandlerRegistry([spy, *builtin_handlers()]),
    ).generate_metadata()

    assert "a/b/probe.spy" in spy.seen["build_croissant"]


@pytest.fixture
def fhir_chunks(tmp_path: Path) -> Path:
    """The shape that produced the phantom includes: several wrapped chunks."""
    chunk = (
        b'{"resourceType": "Patient", "id": "a", "gender": "female"}\n'
        b'{"resourceType": "Patient", "id": "b", "gender": "male"}\n'
    )
    for i in range(3):
        with gzip.open(tmp_path / f"Patient.{i:03d}.ndjson.gz", "wb") as fh:
            fh.write(chunk)
    return tmp_path


def test_the_includes_are_exactly_the_files_on_disk(fhir_chunks: Path) -> None:
    """An exact set, in both directions at once: no pattern that matches
    nothing (the phantom ``.gz.gz`` from expanding an exact path as a glob),
    and no stored file left out of the FileSet describing it."""
    metadata = MetadataGenerator(
        dataset_path=str(fhir_chunks), name="fhir"
    ).generate_metadata()

    resolved = sorted(p for fs in file_sets(metadata) for p in includes(fs))

    assert resolved == sorted(p.name for p in fhir_chunks.iterdir())


def test_a_glob_gains_one_variant_per_wrapper_present(dataset: Path) -> None:
    """And in registry order, so the document does not depend on set hashing."""
    for stem, suffix in (("a.png", ".gz"), ("b.png", ".bz2"), ("c.png", ".xz")):
        write_wrapped(dataset, stem, PNG_1X1, suffix)

    assert includes(file_sets(bake(dataset))[0]) == [
        "**/*.png",
        "**/*.png.gz",
        "**/*.png.bz2",
        "**/*.png.xz",
    ]


def test_a_dataset_of_plain_files_keeps_its_bare_globs(dataset: Path) -> None:
    write_wrapped(dataset, "pixel.png", PNG_1X1)

    assert includes(file_sets(bake(dataset))[0]) == ["**/*.png"]


def test_a_glob_stays_scoped_to_the_directory_it_describes(dataset: Path) -> None:
    """Parquet shards group per directory, so the glob is not rooted at ``**/``."""
    ((_, payload),) = SAMPLES["ParquetHandler"]()
    part = dataset / "part"
    part.mkdir()
    write_wrapped(part, "part-00000.parquet", payload)
    write_wrapped(part, "part-00001.parquet", payload, ".gz")

    assert includes(file_sets(bake(dataset))[0]) == [
        "part/*.parquet",
        "part/*.parquet.gz",
    ]


def test_a_wrapper_elsewhere_does_not_reach_an_unrelated_fileset(
    dataset: Path,
) -> None:
    """The reviewer's case: one wrapper list per handler batch, given to every
    FileSet in it. Nothing in ``data/`` is gzipped, so nothing there may say so."""
    ((_, payload),) = SAMPLES["ParquetHandler"]()
    data = dataset / "data"
    data.mkdir()
    write_wrapped(data, "part-0.parquet", payload)
    write_wrapped(data, "part-1.parquet", payload)
    write_wrapped(dataset, "patients.parquet", payload, ".gz")

    grouped = next(fs for fs in file_sets(bake(dataset)) if fs["@id"] == "data-fileset")

    assert includes(grouped) == ["data/*.parquet"]
    assert grouped["encodingFormat"] == "application/vnd.apache.parquet"


def test_a_glob_does_not_reach_into_a_deeper_directory(dataset: Path) -> None:
    """``data/*.parquet`` is one level of ``data``, not any ``data`` anywhere.

    ``Path.match`` is right-anchored, so it answers this one wrong: it accepts
    ``other/data/x.parquet``.
    """
    ((_, payload),) = SAMPLES["ParquetHandler"]()
    for directory in ("data", "other/data"):
        (dataset / directory).mkdir(parents=True)
        write_wrapped(dataset / directory, "part-0.parquet", payload)
        write_wrapped(dataset / directory, "part-1.parquet", payload)
    write_wrapped(dataset / "other" / "data", "part-2.parquet", payload, ".gz")

    grouped = next(fs for fs in file_sets(bake(dataset)) if fs["@id"] == "data-fileset")

    assert includes(grouped) == ["data/*.parquet"]


def test_a_linked_twin_stays_inside_its_primarys_fileset(dataset: Path) -> None:
    """A duplicate rides with the file it links to, wrapper suffix and all."""
    write_wrapped(dataset, "pixel.png", PNG_1X1)
    write_wrapped(dataset, "pixel.png", PNG_1X1, ".gz")

    assert includes(file_sets(bake(dataset))[0]) == ["**/*.png", "**/*.png.gz"]


def test_an_upper_case_extension_still_reports_its_wrapper(dataset: Path) -> None:
    """Dispatch lowercases the suffix, so ``pixel.PNG.gz`` is described as a
    gzipped PNG. A FileSet that resolved membership by case-sensitive glob text
    would describe it and then claim it arrived uncompressed."""
    write_wrapped(dataset, "pixel.PNG", PNG_1X1, ".gz")

    described = file_sets(bake(dataset))[0]

    assert "application/gzip" in described["encodingFormat"]


def test_a_twin_under_another_extension_joins_its_primarys_fileset(
    dataset: Path,
) -> None:
    """``pixel.jpg`` and ``pixel.jpeg.gz`` hold the same bytes, so one is
    described and the other links to it. The link is the only thing tying the
    twin to the FileSet: no glob the handler declares spells ``.jpeg``."""
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buffer, "JPEG")
    jpeg = buffer.getvalue()
    write_wrapped(dataset, "pixel.jpg", jpeg)
    write_wrapped(dataset, "pixel.jpeg", jpeg, ".gz")

    described = file_sets(bake(dataset))[0]

    assert "application/gzip" in described["encodingFormat"]
    assert "pixel.jpeg.gz" in includes(described)


@pytest.mark.parametrize("suffix", WRAPPER_SUFFIXES)
def test_a_description_names_the_file_a_reader_can_find(
    suffix: str, tmp_path: Path
) -> None:
    """A manifest that names a file not on disk sends the reader nowhere."""
    write_wrapped(tmp_path, "sample.csv", b"id,name\n1,Ada\n", suffix)

    metadata = MetadataGenerator(
        dataset_path=str(tmp_path), name="described"
    ).generate_metadata()

    (record_set,) = metadata["recordSet"]
    stored = f"sample.csv{suffix}"

    assert record_set["@id"] == "sample"
    assert stored in record_set["description"], record_set["description"]
    for field in record_set["field"]:
        assert stored in field["description"], field["description"]


def test_a_plain_file_is_described_exactly_as_before(tmp_path: Path) -> None:
    """The uncompressed path is untouched: no dangling wrapper, no change."""
    (tmp_path / "sample.csv").write_text("id,name\n1,Ada\n")

    metadata = MetadataGenerator(
        dataset_path=str(tmp_path), name="described"
    ).generate_metadata()

    (record_set,) = metadata["recordSet"]
    assert record_set["description"] == "Records from sample.csv"
    assert record_set["field"][0]["description"] == "Column 'id' from sample.csv"
