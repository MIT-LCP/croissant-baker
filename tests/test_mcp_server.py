"""Tests for the local stdio MCP server.

The three tools are plain functions, so they are called directly here rather
than over a transport: the transport is the SDK's business, and driving one
would make these tests slow and non-deterministic for no extra coverage.
"""

import asyncio
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from croissant_baker import mcp_server  # noqa: E402

DATA = Path(__file__).parent / "data" / "input"


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    """A writable copy of a small committed fixture."""
    destination = tmp_path / "gharchive_demo"
    shutil.copytree(DATA / "gharchive_demo", destination)
    return destination


def test_dry_run_reports_claimed_and_refused_with_reasons() -> None:
    """A directory with both describable and undescribable files reports both."""
    report = mcp_server.dry_run(str(DATA / "spect_demo"))

    outcomes = {f["path"]: f for f in report["files"]}
    claimed = [f for f in outcomes.values() if f["outcome"] == "would_process"]
    refused = [f for f in outcomes.values() if f["outcome"] == "unclaimed"]

    assert claimed, "expected the DICOM and NIfTI files to be claimed"
    assert refused, "expected README.md to go unclaimed"
    assert all("reason" in f and "detail" in f for f in refused)
    assert report["total"] == len(claimed) + len(refused)


def test_dry_run_honours_exclude(dataset: Path) -> None:
    """The include/exclude filters reach the scan."""
    everything = mcp_server.dry_run(str(dataset))
    filtered = mcp_server.dry_run(str(dataset), exclude=["*.jsonl.gz"])

    assert everything["total"] > 0
    assert filtered["total"] == 0


def test_bake_writes_a_file_validate_accepts(dataset: Path, tmp_path: Path) -> None:
    """A bake through the tool produces metadata the validate tool accepts."""
    output = tmp_path / "out.jsonld"

    result = mcp_server.bake(
        input_dir=str(dataset),
        output=str(output),
        name="gharchive-demo",
        description="A committed subset of the GH Archive.",
        license="https://creativecommons.org/licenses/by/4.0/",
        creators=["Jane Doe,jane@example.com,https://example.org/jane"],
    )

    assert result["output"] == str(output)
    assert output.is_file()
    assert result["report"]["described"] >= 1
    assert mcp_server.validate(str(output)) == {"valid": True}

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["name"] == "gharchive-demo"
    assert document["creator"]["name"] == "Jane Doe"
    assert document["creator"]["email"] == "jane@example.com"


def test_validate_reports_the_error_on_broken_jsonld(tmp_path: Path) -> None:
    """A document mlcroissant cannot construct comes back with the error text."""
    broken = tmp_path / "broken.jsonld"
    broken.write_text(json.dumps({"@type": "sc:Dataset"}), encoding="utf-8")

    result = mcp_server.validate(str(broken))

    assert result["valid"] is False
    assert isinstance(result["error"], str) and result["error"]


def test_build_server_registers_exactly_the_three_tools() -> None:
    """The surface is deliberately narrow: three tools, no more."""
    tools = asyncio.run(mcp_server.build_server().list_tools())

    assert sorted(tool.name for tool in tools) == ["bake", "dry_run", "validate"]
