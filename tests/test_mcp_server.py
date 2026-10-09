"""Tests for the local stdio MCP server.

The three tools are plain functions, so they are called directly here rather
than over a transport: the transport is the SDK's business, and driving one
would make these tests slow and non-deterministic for no extra coverage.
"""

import asyncio
import importlib.resources
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


def test_dry_run_counters_match_the_per_file_outcomes() -> None:
    """The summary counts what a dry run actually resolves, not what a bake does.

    A dry run reads nothing, so nothing is described; counting it as undescribed
    would report a directory that bakes cleanly as one that describes no file.
    """
    report = mcp_server.dry_run(str(DATA / "spect_demo"))

    assert report["total"] == 7
    assert report["would_process"] == 6
    assert report["unclaimed"] == 1
    assert report["by_reason"] == {"no_handler": 1}
    assert "described" not in report and "undescribed" not in report


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


def test_bake_refuses_a_nameless_creator_with_a_clear_error(
    dataset: Path, tmp_path: Path
) -> None:
    """A creator with no name reaches the client as the refusal, and nothing is written.

    Any exception other than the SDK's ``ToolError`` is reported as a bare
    "Error executing tool bake", which tells the caller nothing to fix.
    """
    from mcp.server.mcpserver.exceptions import ToolError

    output = tmp_path / "out.jsonld"
    arguments = {
        "input_dir": str(dataset),
        "output": str(output),
        "name": "gharchive-demo",
        "description": "A committed subset of the GH Archive.",
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "creators": [",jane@example.com"],
    }

    with pytest.raises(ToolError, match="has no name") as refusal:
        asyncio.run(mcp_server.build_server().call_tool("bake", arguments))

    assert "Example: --creator" in str(refusal.value)
    assert not output.exists()


def test_bake_refuses_an_empty_creator_list(dataset: Path, tmp_path: Path) -> None:
    """``[]`` satisfies the schema, so the refusal has to come from the code.

    Without it the document names the generator's placeholder person, which
    the CLI never allows.
    """
    from mcp.server.mcpserver.exceptions import ToolError

    output = tmp_path / "out.jsonld"
    arguments = {
        "input_dir": str(dataset),
        "output": str(output),
        "name": "gharchive-demo",
        "description": "A committed subset of the GH Archive.",
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "creators": [],
    }

    with pytest.raises(ToolError, match="At least one"):
        asyncio.run(mcp_server.build_server().call_tool("bake", arguments))

    assert not output.exists()


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


def test_the_server_publishes_the_skill_as_its_only_resource() -> None:
    """An agent that connects can read the skill without a filesystem path."""
    resources = asyncio.run(mcp_server.build_server().list_resources())

    assert [str(r.uri) for r in resources] == ["croissant-baker://skill"]
    assert [r.mime_type for r in resources] == ["text/markdown"]


def test_reading_the_skill_resource_returns_the_packaged_skill() -> None:
    """The resource serves the file that ships in the package, verbatim."""
    packaged = (
        importlib.resources.files("croissant_baker")
        .joinpath("skills", "croissant-baker", "SKILL.md")
        .read_text(encoding="utf-8")
    )

    contents = asyncio.run(
        mcp_server.build_server().read_resource("croissant-baker://skill")
    )

    assert "".join(chunk.content for chunk in contents) == packaged


def test_dry_run_on_a_missing_directory_names_it_to_the_client(tmp_path: Path) -> None:
    """A missing input directory reaches the client with its path, as in bake."""
    from mcp.server.mcpserver.exceptions import ToolError

    missing = tmp_path / "no-such-dir"

    with pytest.raises(ToolError, match="no-such-dir"):
        asyncio.run(
            mcp_server.build_server().call_tool("dry_run", {"input_dir": str(missing)})
        )
