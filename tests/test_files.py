"""Tests for file discovery utilities."""

import logging
from pathlib import Path
import pytest
from croissant_baker.files import discover_files


def test_discover_files(tmp_path: Path) -> None:
    """Test discover_files finds files recursively and returns relative paths."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "file1.txt").write_text("test")
    (tmp_path / "sub/file2.txt").write_text("test")

    files = discover_files(str(tmp_path))
    expected = {Path("file1.txt"), Path("sub/file2.txt")}
    assert set(files) == expected


def test_discover_files_empty_directory(tmp_path: Path) -> None:
    """Test discover_files returns empty list for empty directory."""
    files = discover_files(str(tmp_path))
    assert files == []


def test_discover_files_nonexistent_directory() -> None:
    """Test discover_files raises FileNotFoundError for nonexistent directory."""
    with pytest.raises(FileNotFoundError, match="Directory not found"):
        discover_files("/nonexistent/path")


def test_discover_files_not_a_directory(tmp_path: Path) -> None:
    """Test discover_files raises FileNotFoundError for non-directory path."""
    file_path = tmp_path / "file.txt"
    file_path.write_text("test")
    with pytest.raises(FileNotFoundError, match="is not a directory"):
        discover_files(str(file_path))


def test_discover_files_skips_hidden_dirs(tmp_path: Path) -> None:
    """Test discover_files skips files inside hidden directories."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "file1.txt").write_text("test")
    (tmp_path / "sub/file2.txt").write_text("test")

    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden/file3.txt").write_text("test")

    (tmp_path / "sub/.git").mkdir()
    (tmp_path / "sub/.git/file4.txt").write_text("test")

    files = discover_files(str(tmp_path))
    expected = {Path("file1.txt"), Path("sub/file2.txt")}
    assert set(files) == expected


def test_discover_files_caps_skipped_examples(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The skipped-file count is exact, but the example list is bounded.

    Regression guard: previously every hidden-directory path was appended to an
    in-memory list used only for a debug log, so a large hidden tree (e.g. a
    stray .git) grew the list without bound.
    """
    (tmp_path / "keep.txt").write_text("x")
    (tmp_path / ".hidden").mkdir()
    for i in range(20):
        (tmp_path / ".hidden" / f"f{i}.txt").write_text("x")

    with caplog.at_level(logging.DEBUG, logger="croissant_baker.files"):
        files = discover_files(str(tmp_path))

    assert set(files) == {Path("keep.txt")}
    rec = next(r for r in caplog.records if "hidden directories" in r.getMessage())
    assert rec.args[0] == 20  # exact skipped count preserved
    assert len(rec.args[1]) == 5  # example list capped


def test_discover_files_include_patterns(tmp_path: Path) -> None:
    """Test discover_files only returns files matching include patterns."""
    (tmp_path / "data").mkdir()
    (tmp_path / "file1.csv").write_text("test")
    (tmp_path / "file2.txt").write_text("test")
    (tmp_path / "data/file3.csv").write_text("test")

    files = discover_files(str(tmp_path), include_patterns=["*.csv"])
    expected = {Path("file1.csv"), Path("data/file3.csv")}
    assert set(files) == expected


def test_discover_files_exclude_patterns(tmp_path: Path) -> None:
    """Test discover_files ignores files matching exclude patterns."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "file1.csv").write_text("test")
    (tmp_path / "temp.csv").write_text("test")
    (tmp_path / "tests/test_file.csv").write_text("test")

    files = discover_files(str(tmp_path), exclude_patterns=["temp.csv", "tests/*"])
    expected = {Path("file1.csv")}
    assert set(files) == expected


def test_discover_files_include_and_exclude(tmp_path: Path) -> None:
    """Test discover_files applies include first, then exclude."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "data1.csv").write_text("test")
    (tmp_path / "data2.parquet").write_text("test")
    (tmp_path / "temp.csv").write_text("test")
    (tmp_path / "sub/data3.csv").write_text("test")
    (tmp_path / "sub/temp.csv").write_text("test")

    files = discover_files(
        str(tmp_path),
        include_patterns=["*.csv"],
        exclude_patterns=["temp.csv", "sub/temp.csv"],
    )
    expected = {Path("data1.csv"), Path("sub/data3.csv")}
    assert set(files) == expected
