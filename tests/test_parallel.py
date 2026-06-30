"""Parallel extraction must not change output and must respect ``max_workers``.

The extraction loop in ``MetadataGenerator.generate_metadata`` runs on a thread
pool, but results are reassembled in discovery order before any @id is assigned.
These tests pin that invariant: the same dataset bakes to byte-identical metadata
regardless of worker count, and the progress callback reports one event per file.
"""

import json
from pathlib import Path

import pytest

from croissant_baker.metadata_generator import MetadataGenerator, serialize_datetime


@pytest.fixture
def mimic_demo_dir() -> Path:
    path = (
        Path(__file__).parent
        / "data"
        / "input"
        / "mimiciv_demo"
        / "physionet.org"
        / "files"
        / "mimic-iv-demo"
        / "2.2"
    )
    if not path.exists():
        pytest.skip(f"MIMIC-IV demo dataset not found at {path}")
    return path


def _dump(meta: dict) -> str:
    return json.dumps(
        meta, indent=2, ensure_ascii=False, sort_keys=True, default=serialize_datetime
    )


def _bake(path: Path, workers: int) -> dict:
    # Supply date + creators so the comparison isolates worker-count effects
    # from any other default.
    return MetadataGenerator(
        str(path),
        name="Parallel DS",
        description="d",
        creators=[{"name": "A. Author"}],
        date_published="2024-01-01",
        max_workers=workers,
    ).generate_metadata()


@pytest.mark.parametrize("workers", [2, 4, 8])
def test_output_identical_across_worker_counts(
    mimic_demo_dir: Path, workers: int
) -> None:
    """Parallel runs produce byte-identical metadata to the serial run."""
    serial = _dump(_bake(mimic_demo_dir, 1))
    parallel = _dump(_bake(mimic_demo_dir, workers))
    assert parallel == serial


def test_progress_callback_reports_each_file_once(mimic_demo_dir: Path) -> None:
    """The callback fires once per file and the final event reports completion."""
    calls: list[tuple[int, int]] = []
    MetadataGenerator(
        str(mimic_demo_dir),
        name="X",
        description="d",
        creators=[{"name": "A"}],
        max_workers=4,
    ).generate_metadata(progress_callback=lambda c, t, p: calls.append((c, t)))

    total = calls[-1][1]
    assert len(calls) == total  # one event per discovered file
    assert calls[-1][0] == total  # final event reports all files complete
    assert {c for c, _ in calls} == set(range(1, total + 1))  # 1..total, no dupes


def test_resolve_worker_count(mimic_demo_dir: Path) -> None:
    """Explicit worker counts are floored at 1; auto-sizing stays serial for ≤1 file."""
    gen = MetadataGenerator(
        str(mimic_demo_dir), name="X", description="d", creators=[{"name": "A"}]
    )

    gen.max_workers = 1
    assert gen._resolve_worker_count(100) == 1

    gen.max_workers = 0  # nonsensical explicit value → floored to serial
    assert gen._resolve_worker_count(100) == 1

    gen.max_workers = None  # auto
    assert gen._resolve_worker_count(0) == 1
    assert gen._resolve_worker_count(1) == 1
    assert gen._resolve_worker_count(100) >= 1
