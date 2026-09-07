"""The CLI states coverage: how much was described, and how to see what was not."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from croissant_baker.__main__ import app

from tests.helpers import cli, runner


def _run(dataset: Path, out_dir: Path, *extra: str):
    """Bake ``dataset``, writing into ``out_dir``, with the standard flag set."""
    return cli(dataset, out_dir / "out.jsonld", *extra)


@pytest.fixture
def mixed_dataset(tmp_path: Path) -> Path:
    """One described file, one unclaimed, one that fails mid-parse."""
    dataset = tmp_path / "mixed"
    dataset.mkdir()
    (dataset / "table.csv").write_text("id,name\n1,Alice\n2,Bob\n")
    (dataset / "opaque.bin").write_bytes(b"\x00\x01\x02")
    (dataset / "broken.json").write_text("{not valid json")
    return dataset


def test_summary_states_described_and_undescribed_counts(
    mixed_dataset: Path, tmp_path: Path
) -> None:
    result = _run(mixed_dataset, tmp_path)

    assert result.exit_code == 0
    assert "Scanned 3 file(s): 1 described, 2 not described." in result.stdout
    assert "no handler: 1" in result.stdout
    assert "extraction failed: 1" in result.stdout


@pytest.mark.parametrize(
    ("kind", "write"),
    [
        # Both kinds, because an unclaimed file fails silently while a failing
        # one is loud — and only the loud case can leak a line per file.
        ("unclaimed", lambda p: p.with_suffix(".bin").write_bytes(b"\x00\x01")),
        ("failing", lambda p: p.with_suffix(".csv").write_text("")),
    ],
)
def test_the_coverage_section_does_not_grow_with_the_dataset(
    kind: str, write, tmp_path: Path
) -> None:
    """The coverage section is bounded by reasons, not by files."""

    def coverage_for(n: int) -> list[str]:
        dataset = tmp_path / f"{kind}{n}"
        dataset.mkdir()
        (dataset / "table.csv").write_text("id\n1\n")
        for i in range(n):
            write(dataset / f"undescribed{i}")
        result = _run(dataset, dataset)
        assert result.exit_code == 0
        return (result.stdout + result.stderr).splitlines()

    few, many = coverage_for(2), coverage_for(60)

    assert len(few) == len(many), "\n".join(many)
    assert any("60 not described" in line for line in many)


def test_a_default_run_names_no_undescribed_file(
    mixed_dataset: Path, tmp_path: Path
) -> None:
    """Quiet by default: the summary counts them, nothing lists them.

    Run out of process. In-process the check is vacuous: pytest's logging
    plugin holds a root handler, so ``logging.lastResort`` never fires and a
    stray warning never reaches the stderr a runner would capture.
    """
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "croissant_baker",
            "--input",
            str(mixed_dataset),
            "--output",
            str(tmp_path / "out.jsonld"),
            "--creator",
            "Tester",
            "--no-validate",
        ],
        capture_output=True,
        text=True,
    )

    assert done.returncode == 0, done.stderr
    everything = done.stdout + done.stderr
    assert "no handler for file type" not in everything
    assert "opaque.bin" not in everything
    assert "broken.json" not in everything


def test_verbose_paths_are_dataset_relative_and_appear_in_the_report(
    mixed_dataset: Path, tmp_path: Path
) -> None:
    """The path a reader is shown is the key they can look up in --report."""
    report_path = tmp_path / "scan.json"
    result = _run(mixed_dataset, tmp_path, "--verbose", "--report", str(report_path))

    assert result.exit_code == 0
    known = {f["path"] for f in json.loads(report_path.read_text())["files"]}
    shown = [
        line.split("File: ", 1)[1].strip()
        for line in result.stdout.splitlines()
        if "File: " in line
    ]

    assert shown, result.stdout
    assert set(shown) <= known, (shown, known)
    assert not any(Path(p).is_absolute() for p in shown)


def test_undescribed_files_prompt_for_detail(
    mixed_dataset: Path, tmp_path: Path
) -> None:
    result = _run(mixed_dataset, tmp_path)
    assert "Tip: re-run with --verbose, or --report FILE" in result.stdout


def test_verbose_names_every_undescribed_file_and_reason(
    mixed_dataset: Path, tmp_path: Path
) -> None:
    result = _run(mixed_dataset, tmp_path, "--verbose")

    assert result.exit_code == 0
    assert "no handler for file type. File: " in result.stdout
    assert "opaque.bin" in result.stdout
    assert "broken.json" in result.stdout
    # the prompt is redundant once detail has been printed
    assert "Tip: re-run with --verbose" not in result.stdout


def test_report_names_every_file_with_its_outcome(
    mixed_dataset: Path, tmp_path: Path
) -> None:
    report_path = tmp_path / "scan.json"
    result = _run(mixed_dataset, tmp_path, "--report", str(report_path))

    assert result.exit_code == 0
    assert f"Scan report: {report_path}" in result.stdout

    payload = json.loads(report_path.read_text())
    assert payload["total"] == 3
    assert payload["described"] == 1
    assert payload["undescribed"] == 2

    files = {f["path"]: f for f in payload["files"]}
    assert files["table.csv"]["outcome"] == "described"
    assert files["opaque.bin"]["outcome"] == "unclaimed"
    assert files["opaque.bin"]["reason"]
    assert files["broken.json"]["outcome"] == "failed"
    assert files["broken.json"]["reason"]


def test_report_replaces_the_detail_prompt(mixed_dataset: Path, tmp_path: Path) -> None:
    result = _run(mixed_dataset, tmp_path, "--report", str(tmp_path / "scan.json"))
    assert "Tip: re-run with --verbose" not in result.stdout


def test_dry_run_lists_files_that_would_not_be_described(
    mixed_dataset: Path,
) -> None:
    result = runner.invoke(app, ["--input", str(mixed_dataset), "--dry-run"])

    assert result.exit_code == 0
    assert "would be processed" in result.stdout
    assert "table.csv" in result.stdout
    assert "1 file(s) would not be described" in result.stdout
    assert "no handler for file type. File: " in result.stdout
    assert "opaque.bin" in result.stdout


def test_a_dry_run_report_says_what_would_happen_not_that_nothing_did(
    mixed_dataset: Path, tmp_path: Path
) -> None:
    """A claimed file is not pending, and it is not undescribed either."""
    report_path = tmp_path / "dry.json"
    result = runner.invoke(
        app,
        ["--input", str(mixed_dataset), "--dry-run", "--report", str(report_path)],
    )

    assert result.exit_code == 0
    payload = json.loads(report_path.read_text())
    outcomes = {f["path"]: f["outcome"] for f in payload["files"]}

    assert outcomes["table.csv"] == "would_process"
    assert outcomes["broken.json"] == "would_process"
    assert outcomes["opaque.bin"] == "unclaimed"
    assert "pending" not in outcomes.values()


def test_bake_with_no_describable_files_names_the_reason(tmp_path: Path) -> None:
    dataset = tmp_path / "opaque"
    dataset.mkdir()
    (dataset / "a.bin").write_bytes(b"\x00")
    (dataset / "b.bin").write_bytes(b"\x01")

    result = _run(dataset, tmp_path)

    assert result.exit_code == 1
    assert "No supported files found in the dataset" in result.stderr
    # The coverage summary still explains which files were found, and why none
    # of them could be described.
    assert "Scanned 2 file(s): 0 described, 2 not described." in result.stdout
    assert "no handler: 2" in result.stdout


def test_failed_bake_still_writes_a_report(tmp_path: Path) -> None:
    """Coverage is most useful precisely when nothing could be described."""
    dataset = tmp_path / "opaque"
    dataset.mkdir()
    (dataset / "a.bin").write_bytes(b"\x00")

    report_path = tmp_path / "scan.json"
    result = _run(dataset, tmp_path, "--report", str(report_path))

    assert result.exit_code == 1
    payload = json.loads(report_path.read_text())
    assert payload["described"] == 0
    assert payload["files"][0]["outcome"] == "unclaimed"


def test_the_report_carries_every_bucket_and_the_parent_of_a_referenced_file(
    tmp_path: Path,
) -> None:
    """A tool reading --report must be able to tell the three ways in apart."""
    mitdb = (
        Path(__file__).parent
        / "data"
        / "input"
        / "mitdb_wfdb"
        / "physionet.org"
        / "files"
        / "mitdb"
        / "1.0.0"
    )
    dataset = tmp_path / "record"
    dataset.mkdir()
    for name in ("100.hea", "100.dat", "100.atr"):
        (dataset / name).write_bytes((mitdb / name).read_bytes())
    (dataset / "twin.csv").write_text("id\n1\n")
    (dataset / "twin.csv.gz").write_bytes(__import__("gzip").compress(b"id\n1\n"))

    report_path = tmp_path / "scan.json"
    result = _run(dataset, tmp_path, "--report", str(report_path))
    assert result.exit_code == 0

    payload = json.loads(report_path.read_text())
    assert payload["described"] == 2
    assert payload["linked"] == 1
    assert payload["referenced"] == 2
    assert payload["undescribed"] == 0

    files = {f["path"]: f for f in payload["files"]}
    assert files["100.dat"]["outcome"] == "referenced"
    assert files["100.dat"]["part_of"] == "100.hea"
    assert files["twin.csv.gz"]["duplicate_of"] == "twin.csv"
