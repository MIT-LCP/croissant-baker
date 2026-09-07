"""Tests for WFDB handler."""

from pathlib import Path
from unittest.mock import patch

import pytest

from croissant_baker.handlers.wfdb_handler import WFDBHandler
from croissant_baker.sources import make_source


@pytest.fixture
def wfdb_sample_path():
    """Path to a WFDB record for unit testing."""
    path = (
        Path(__file__).parent
        / "data"
        / "input"
        / "mitdb_wfdb"
        / "physionet.org"
        / "files"
        / "mitdb"
        / "1.0.0"
        / "100.hea"
    )
    if not path.exists():
        pytest.skip(f"WFDB sample data not found at {path}")
    return path


def test_can_handle_hea_file(wfdb_sample_path):
    handler = WFDBHandler()
    assert handler.claims(make_source(wfdb_sample_path, with_path=True))


def test_extract_metadata(wfdb_sample_path):
    handler = WFDBHandler()
    metadata = handler.extract(make_source(wfdb_sample_path, with_path=True))

    assert metadata["record_name"] == "100"
    assert metadata["encoding_format"] == "application/x-wfdb-header"
    assert metadata["num_signals"] == 2
    assert metadata["sampling_frequency"] == 360
    assert metadata["num_samples"] == 650000
    assert "MLII" in metadata["signal_names"]
    assert "V5" in metadata["signal_names"]
    # We expect .dat and .atr files to be found alongside .hea
    assert len(metadata["related_files"]) == 2
    assert metadata["signal_types"]["MLII"] == "sc:Float"
    assert metadata["signal_types"]["V5"] == "sc:Float"


def test_missing_dat_file_raises_error(tmp_path):
    handler = WFDBHandler()
    hea_file = tmp_path / "test.hea"
    hea_file.write_text("test 1 360 1000")

    with pytest.raises(ValueError, match="WFDB data file missing"):
        handler.extract(make_source(hea_file, with_path=True))


def test_rdheader_failure_is_wrapped(tmp_path):
    handler = WFDBHandler()
    hea_file = tmp_path / "test.hea"
    hea_file.write_text("test 1 360 1000")

    with patch(
        "croissant_baker.handlers.wfdb_handler.wfdb.rdheader",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(ValueError, match="Failed to read WFDB header"):
            handler.extract(make_source(hea_file, with_path=True))


def test_wfdb_build_croissant() -> None:
    handler = WFDBHandler()
    meta = {
        "record_name": "100",
        "signal_types": {"MLII": "sc:Float", "V5": "sc:Float"},
        "num_signals": 2,
        "sampling_frequency": 360,
        "num_samples": 650000,
        "duration_seconds": 1805.56,
    }
    filesets, record_sets = handler.build_croissant([meta], ["file_0"])

    assert filesets == []
    assert len(record_sets) == 1
    assert record_sets[0].name == "100"
    assert {f.name for f in record_sets[0].fields} == {"MLII", "V5"}
