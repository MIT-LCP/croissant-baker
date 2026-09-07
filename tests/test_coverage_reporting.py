"""Every file is accounted for, and one failure costs one file."""

import hashlib
import lzma
import subprocess
import sys
from pathlib import Path

import pytest

from croissant_baker.handlers.base_handler import BuildResult, Declined, FileTypeHandler
from croissant_baker.handlers.csv_handler import CSVHandler
from croissant_baker.handlers.registry import HandlerRegistry
from croissant_baker.metadata_generator import MetadataGenerator
from croissant_baker.scan import Outcome, Reason, ScanEntry, ScanReport

from tests.helpers import (
    DATA,
    bake_with,
    bake_with_report,
    file_objects,
    write_wrapped,
)

CSV = b"id,name\n1,Ada\n2,Grace\n"

HEADER = b"record 1 1 10\nrecord.dat 16 200\n"


@pytest.fixture
def refusals(dataset: Path) -> Path:
    """One directory holding every shape of undescribed file, plus two good ones."""
    write_wrapped(dataset, "good.csv", CSV)
    # Nothing claims this extension.
    write_wrapped(dataset, "mystery.xyz", b"?")
    # Claimed by extension, then fails once nibabel reads the header.
    write_wrapped(dataset, "broken.nii", b"not a nifti header" * 4)
    # A multi-member archive, which the baker reports and does not open.
    write_wrapped(dataset, "bundle.zip", b"PK\x03\x04not-really-a-zip")
    # WFDB reads a header together with its siblings, so it needs a real path.
    write_wrapped(dataset, "record.hea", HEADER, ".gz")
    # One file in two forms.
    write_wrapped(dataset, "twin.csv", CSV)
    write_wrapped(dataset, "twin.csv", CSV, ".gz")
    return dataset


@pytest.fixture
def report(refusals: Path) -> ScanReport:
    return bake_with_report(refusals)[1]


def _entry(report: ScanReport, name: str):
    return next(e for e in report.entries if e.name == name)


def test_the_rest_of_the_directory_is_still_described(report: ScanReport) -> None:
    """The loss is per-file, never the whole bake."""
    assert sorted(e.name for e in report.described) == ["good.csv", "twin.csv"]


WORKING = {Outcome.PENDING, Outcome.READY, Outcome.WOULD_PROCESS}


def test_every_discovered_file_is_accounted_for(report: ScanReport) -> None:
    """PENDING, READY and WOULD_PROCESS are internal; none may reach a report."""
    assert report.total == 7
    assert [e.name for e in report.entries if e.outcome in WORKING] == []
    assert all(e.reason is not None for e in report.undescribed)


@pytest.mark.parametrize(
    ("name", "reason", "in_detail"),
    [
        ("mystery.xyz", Reason.NO_HANDLER, ["no handler"]),
        ("broken.nii", Reason.EXTRACT_FAILED, ["broken.nii"]),
        ("bundle.zip", Reason.ARCHIVE, ["does not open archives"]),
        ("record.hea.gz", Reason.UNSUPPORTED_INPUT, ["WFDB", "on disk", "gzip"]),
        ("twin.csv.gz", Reason.DUPLICATE_BY_NAME, ["twin.csv", "not verified"]),
    ],
)
def test_each_refusal_carries_its_own_reason_and_evidence(
    report: ScanReport, name: str, reason: Reason, in_detail: list
) -> None:
    """Folding any pair of these into one reason loses what a user acts on."""
    entry = _entry(report, name)
    assert entry.reason is reason
    for fragment in in_detail:
        assert fragment in entry.detail, entry.detail


def test_the_machine_readable_report_carries_all_of_them(report: ScanReport) -> None:
    """The report is machine-readable, so a tool can act on it."""
    files = {f["path"]: f for f in report.to_dict()["files"]}

    assert len(files) == 7
    assert all("reason" in files[e.name] for e in report.undescribed)
    assert files["good.csv"]["outcome"] == "described"
    assert files["bundle.zip"]["reason"] == "archive"
    assert files["twin.csv.gz"]["duplicate_of"] == "twin.csv"


def test_a_compressed_archive_is_still_an_archive(dataset: Path) -> None:
    """The wrapper is stripped first, so every compression composes with .tar."""
    write_wrapped(dataset, "good.csv", CSV)
    write_wrapped(dataset, "bundle.tar", b"not-really-a-tar", ".gz")

    _, report = bake_with_report(dataset)

    assert _entry(report, "bundle.tar.gz").reason is Reason.ARCHIVE


@pytest.fixture
def corrupt_wrapper(dataset: Path) -> Path:
    """A good CSV beside a file whose wrapper cannot be decompressed at all."""
    write_wrapped(dataset, "good.csv", CSV)
    write_wrapped(dataset, "bad.parquet.xz", b"not xz, just noise" * 8)
    return dataset


@pytest.mark.parametrize("workers", [1, 4])
def test_a_corrupt_wrapper_does_not_abort_the_bake(
    corrupt_wrapper: Path, workers: int
) -> None:
    """One unreadable file must not cost the whole directory."""
    metadata, report = bake_with_report(corrupt_wrapper, max_workers=workers)

    assert [rs["@id"] for rs in metadata["recordSet"]] == ["good"]
    assert _entry(report, "good.csv").outcome is Outcome.DESCRIBED
    assert _entry(report, "bad.parquet.xz").outcome is Outcome.FAILED


def test_a_claim_failure_is_reported_as_its_own_reason(corrupt_wrapper: Path) -> None:
    """Distinct from an extraction failure: the file never reached a handler.

    The entry does not keep the exception object. What the exception *said*
    has to survive, or the report cannot explain itself.
    """
    with pytest.raises(Exception) as raised:  # noqa: PT011 - lzma's own type
        with lzma.open(corrupt_wrapper / "bad.parquet.xz") as fh:
            fh.read(1)

    entry = _entry(bake_with_report(corrupt_wrapper)[1], "bad.parquet.xz")

    assert entry.reason is Reason.CLAIM_FAILED
    assert entry.detail == str(raised.value)


class _BrokenCSVHandler(CSVHandler):
    """Extracts happily, then fails to assemble — the gap the report missed."""

    def build_croissant(self, file_metas, file_ids):
        raise RuntimeError("assembly exploded")


@pytest.fixture
def broken_batch(dataset: Path) -> Path:
    write_wrapped(dataset, "a.csv", CSV)
    write_wrapped(dataset, "b.jsonl", b'{"x": 1}\n{"x": 2}\n')
    return dataset


def test_an_assembly_failure_costs_its_batch_and_nothing_else(
    broken_batch: Path,
) -> None:
    """Not described, no orphan FileObject, and the other handler unaffected."""
    metadata, report = bake_with([_BrokenCSVHandler()], broken_batch)

    assert _entry(report, "a.csv").outcome is Outcome.FAILED
    assert _entry(report, "a.csv").reason is Reason.BUILD_FAILED
    assert "assembly exploded" in _entry(report, "a.csv").detail
    # A FileObject with no record set is worse than no entry at all.
    assert [d["name"] for d in file_objects(metadata)] == ["b.jsonl"]
    assert [rs["@id"] for rs in metadata["recordSet"]] == ["b.jsonl"]
    assert _entry(report, "b.jsonl").outcome is Outcome.DESCRIBED


def test_every_batch_failing_is_an_error_not_an_empty_document(
    dataset: Path,
) -> None:
    from croissant_baker.handlers.registry import builtin_handlers

    write_wrapped(dataset, "only.csv", CSV)
    generator = MetadataGenerator(
        dataset_path=str(dataset),
        name="t",
        handlers=HandlerRegistry([_BrokenCSVHandler(), *builtin_handlers()]),
    )

    with pytest.raises(ValueError, match="No supported files"):
        generator.generate_metadata()

    assert _entry(generator.scan_report, "only.csv").outcome is Outcome.FAILED


class _BadReturnHandler(FileTypeHandler):
    """Claims ``.brk`` and returns whatever shape the test asks for."""

    EXTENSIONS = (".brk",)
    FORMAT_NAME = "Broken"

    def __init__(self, built) -> None:
        self._built = built

    def claims(self, source) -> bool:
        return source.suffix == ".brk"

    def extract(self, source, **kwargs) -> dict:
        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": "application/x-brk",
        }

    def build_croissant(self, file_metas, file_ids):
        return self._built


@pytest.mark.parametrize(
    "built",
    [
        ([], [], "valid"),
        ([], [], [("not-an-index", "nope")]),
        ([], [], [(0,)]),
        "not a tuple at all",
        ([],),
        ([], [], [(99, Reason.EXTRACT_FAILED, "past the end")]),
        ([], [], [(-1, Reason.EXTRACT_FAILED, "counts backwards")]),
        ([], [], [(0, "not-a-reason", "unknown category")]),
        BuildResult([], [], (Declined(99, Reason.EXTRACT_FAILED, "past the end"),)),
    ],
)
def test_a_malformed_handler_return_costs_its_batch_only(built, dataset) -> None:
    """Shape, index and reason are all checked inside the guard. Outside it, a
    handler naming a file it was never given crashes the run, or rejects one it
    did not mean."""
    write_wrapped(dataset, "probe1.brk", b"payload")
    write_wrapped(dataset, "probe2.brk", b"payload")
    write_wrapped(dataset, "keep.csv", CSV)

    metadata, report = bake_with([_BadReturnHandler(built)], dataset)

    for name in ("probe1.brk", "probe2.brk"):
        assert _entry(report, name).outcome is Outcome.FAILED
        assert _entry(report, name).reason is Reason.BUILD_FAILED
    assert [rs["@id"] for rs in metadata["recordSet"]] == ["keep"]


def test_the_library_writes_nothing_to_stderr(tmp_path: Path) -> None:
    """A library that logs to a terminal it does not own is a library bug.

    Out of process, because pytest holds a root handler: in-process,
    ``logging.lastResort`` never fires and the check cannot fail.
    """
    dataset = tmp_path / "quiet"
    dataset.mkdir()
    write_wrapped(dataset, "good.csv", CSV)
    write_wrapped(dataset, "mystery.xyz", b"?")

    done = subprocess.run(
        [
            sys.executable,
            "-W",
            "ignore",
            "-c",
            "from croissant_baker.metadata_generator import MetadataGenerator;"
            f"MetadataGenerator(dataset_path={str(dataset)!r}, name='t')"
            ".generate_metadata()",
        ],
        capture_output=True,
        text=True,
    )

    assert done.returncode == 0, done.stderr
    assert done.stderr == "", done.stderr


MITDB = DATA / "mitdb_wfdb" / "physionet.org" / "files" / "mitdb" / "1.0.0"


@pytest.fixture
def wfdb_record(dataset: Path) -> Path:
    """One WFDB record — a header read together with its .dat and .atr — and a CSV.

    The shape behind the reviewer's MIT-BIH count: nothing claims a .dat on its
    own, so it is UNCLAIMED, but the WFDB handler emits a FileObject for it and
    the document carries it.
    """
    for name in ("100.hea", "100.dat", "100.atr"):
        (dataset / name).write_bytes((MITDB / name).read_bytes())
    write_wrapped(dataset, "good.csv", CSV)
    return dataset


def test_a_file_the_document_carries_is_not_undescribed(wfdb_record: Path) -> None:
    """The defect the reviewer found: a file with a FileObject counted as missing."""
    metadata, report = bake_with_report(wfdb_record)

    urls = {f["contentUrl"] for f in file_objects(metadata)}
    assert {"100.dat", "100.atr"} <= urls

    assert sorted(e.name for e in report.referenced) == ["100.atr", "100.dat"]
    assert report.undescribed == []
    assert _entry(report, "100.dat").outcome is Outcome.REFERENCED


def test_a_referenced_file_names_the_record_that_carries_it(
    wfdb_record: Path,
) -> None:
    """Knowing a file is in the document is only useful with the parent."""
    entry = _entry(bake_with_report(wfdb_record)[1], "100.dat")

    assert entry.part_of is not None
    assert entry.part_of.name == "100.hea"
    assert "100.hea" in entry.detail
    assert entry.reason is None


def test_the_summary_counts_what_the_document_holds(wfdb_record: Path) -> None:
    report = bake_with_report(wfdb_record)[1]

    assert report.summary_lines()[0] == (
        "Scanned 4 file(s): 2 described, 2 referenced, 0 not described."
    )


def test_a_linked_twin_is_counted_as_linked_not_missing(dataset: Path) -> None:
    """A .csv.gz beside its .csv has a FileObject with sameAs; it is in there."""
    write_wrapped(dataset, "twin.csv", CSV)
    write_wrapped(dataset, "twin.csv", CSV, ".gz")

    report = bake_with_report(dataset)[1]

    assert report.summary_lines() == [
        "Scanned 2 file(s): 1 described, 1 linked, 0 not described."
    ]


@pytest.mark.parametrize("fixture", ["wfdb_record", "refusals"])
def test_reason_lines_account_for_the_files_not_in_the_document(
    fixture: str, request: pytest.FixtureRequest
) -> None:
    """A reason line is about a failure. Every failure gets exactly one."""
    report = bake_with_report(request.getfixturevalue(fixture))[1]

    assert sum(report.counts().values()) == len(report.undescribed)


@pytest.mark.parametrize("fixture", ["wfdb_record", "refusals"])
def test_the_report_and_the_document_agree_both_ways(
    fixture: str, request: pytest.FixtureRequest
) -> None:
    """Neither a file in the document counted as missing, nor the reverse."""
    metadata, report = bake_with_report(request.getfixturevalue(fixture))

    urls = {f["contentUrl"] for f in file_objects(metadata)}
    in_document = {
        str(e.path) for e in report.described + report.linked + report.referenced
    }

    assert in_document == urls


def test_a_described_file_cannot_be_demoted_to_referenced() -> None:
    """The lifecycle is a guard, not a suggestion."""
    entry = ScanEntry(path=Path("a.csv"))
    entry.ready(handler=None, meta={})
    entry.describe()

    with pytest.raises(ValueError, match="cannot move from described"):
        entry.referenced(ScanEntry(path=Path("b.hea")))


class _PairHandler(CSVHandler):
    """Describes ``good.csv`` and emits ``bad.csv`` alongside it as a related
    FileObject, while ``bad.csv`` fails to read on its own."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root

    def extract(self, source, **kwargs):
        if source.name == "bad.csv":
            raise ValueError("bad.csv cannot be read on its own")
        meta = super().extract(source, **kwargs)
        sibling = self._root / "bad.csv"
        meta["related_files"] = [
            {
                "path": str(sibling),
                "name": "bad.csv",
                "encoding": "text/csv",
                "size": sibling.stat().st_size,
                "sha256": hashlib.sha256(sibling.read_bytes()).hexdigest(),
            }
        ]
        return meta


@pytest.fixture
def failed_but_emitted(dataset: Path) -> Path:
    write_wrapped(dataset, "good.csv", CSV)
    write_wrapped(dataset, "bad.csv", CSV)
    return dataset


def test_a_file_that_failed_alone_is_still_in_the_document(
    failed_but_emitted: Path,
) -> None:
    """Membership of the document is not the same question as whether the file
    could be read on its own. A handler that emits it anyway puts it in."""
    metadata, report = bake_with([_PairHandler(failed_but_emitted)], failed_but_emitted)

    assert "bad.csv" in {f["contentUrl"] for f in file_objects(metadata)}
    assert _entry(report, "bad.csv").outcome is Outcome.REFERENCED
    assert report.undescribed == []
    assert report.summary_lines()[0] == (
        "Scanned 2 file(s): 1 described, 1 referenced, 0 not described."
    )


def test_a_referenced_file_keeps_what_its_own_failure_said(
    failed_but_emitted: Path,
) -> None:
    """The diagnostic is the only record that reading it was even attempted."""
    _, report = bake_with([_PairHandler(failed_but_emitted)], failed_but_emitted)
    entry = _entry(report, "bad.csv")

    assert "good.csv" in entry.detail
    assert "cannot be read on its own" in entry.detail
    assert entry.reason is None
