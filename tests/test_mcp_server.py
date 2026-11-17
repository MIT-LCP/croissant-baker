"""Tests for the local stdio MCP server.

The three tools are plain functions, so they are called directly here rather
than over a transport: the transport is the SDK's business, and driving one
would make these tests slow and non-deterministic for no extra coverage.
"""

import asyncio
import importlib.resources
import json
import os
import re
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


def test_dry_run_lists_only_the_refused_files_with_reasons() -> None:
    """The counters cover every file; the list covers only what needs a decision.

    Listing every claimed file too would flood the agent on a large tree at the
    first step of the loop, and a claimed file needs nothing from it.
    """
    report = mcp_server.dry_run(str(DATA / "spect_demo"))

    assert [f["path"] for f in report["files"]] == ["README.md"]
    assert all(f["outcome"] == "unclaimed" for f in report["files"])
    assert all("reason" in f and "detail" in f for f in report["files"])
    assert report["would_process"] == 6


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


def test_dry_run_given_the_output_counts_what_bake_will(dataset: Path) -> None:
    """After a bake into the dataset, dry_run must not count the bake's files.

    Passed the same output, it skips the same exact paths bake does, so the
    two totals agree.
    """
    output = dataset / "croissant.jsonld"
    mcp_server.bake(input_dir=str(dataset), output=str(output), **REQUIRED)

    predicted = mcp_server.dry_run(str(dataset), output=str(output))
    baked = mcp_server.bake(input_dir=str(dataset), output=str(output), **REQUIRED)

    assert predicted["total"] == baked["total"]
    assert mcp_server.dry_run(str(dataset))["total"] == baked["total"] + 2


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

    assert result["output"] == str(output.resolve())
    assert output.is_file()
    assert result["described"] >= 1
    assert mcp_server.validate(str(output)) == {"valid": True}

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["name"] == "gharchive-demo"
    assert document["creator"]["name"] == "Jane Doe"
    assert document["creator"]["email"] == "jane@example.com"


REQUIRED = {
    "name": "rel",
    "description": "Two related tables.",
    "license": "https://creativecommons.org/licenses/by/4.0/",
    "creators": ["Jane Doe"],
}


def test_bake_returns_counters_and_leaves_the_file_list_on_disk(
    dataset: Path, tmp_path: Path
) -> None:
    """A large tree must not flood the agent with one entry per file.

    The tool result carries the fixed-size counters, as the CLI's summary does,
    and the full per-file report goes to a file next to the output.
    """
    output = tmp_path / "out.jsonld"

    result = mcp_server.bake(input_dir=str(dataset), output=str(output), **REQUIRED)

    assert "files" not in result
    assert set(result) >= {"total", "described", "undescribed", "by_reason"}
    report_path = Path(result["report_path"])
    assert report_path == (tmp_path / "out.report.json").resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert len(report["files"]) == result["total"]


def test_bake_into_the_dataset_directory_can_run_again(dataset: Path) -> None:
    """A second bake must not describe the first bake's own files.

    The report is JSON, so the JSON handler would claim it on the next run and
    mlcroissant would refuse the record set built from it.
    """
    output = dataset / "croissant.jsonld"

    mcp_server.bake(input_dir=str(dataset), output=str(output), **REQUIRED)
    first = output.read_bytes()
    again = mcp_server.bake(input_dir=str(dataset), output=str(output), **REQUIRED)

    report = json.loads(Path(again["report_path"]).read_text(encoding="utf-8"))
    assert output.read_bytes() == first
    paths = {f["path"] for f in report["files"]}
    assert not paths & {"croissant.jsonld", "croissant.report.json"}


@pytest.mark.parametrize(
    "output_name, data_name",
    [
        ("metadata.json", "metadata.json"),
        ("croissant.jsonld", "croissant.report.json"),
    ],
    ids=["output-name", "report-name"],
)
def test_bake_skips_only_its_own_files_not_data_of_the_same_name(
    tmp_path: Path, output_name: str, data_name: str
) -> None:
    """The skip is the exact path written, never a name match.

    A file deeper in the tree that shares a name with the output or the report
    is data, and dropping it would skip it with no reason given.
    """
    data = tmp_path / "ds"
    (data / "sub").mkdir(parents=True)
    (data / "sub" / data_name).write_text('[{"a": 1}, {"a": 2}]\n')
    output = data / output_name

    mcp_server.bake(input_dir=str(data), output=str(output), **REQUIRED)
    again = mcp_server.bake(input_dir=str(data), output=str(output), **REQUIRED)

    report = json.loads(Path(again["report_path"]).read_text(encoding="utf-8"))
    outcomes = {f["path"]: f["outcome"] for f in report["files"]}
    assert outcomes == {f"sub/{data_name}": "described"}


def test_bake_returns_absolute_paths_for_a_relative_output(
    dataset: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative output resolves against the server's working directory.

    The agent cannot see that directory, so the result names the real path.
    """
    monkeypatch.chdir(tmp_path)

    result = mcp_server.bake(input_dir=str(dataset), output="out.jsonld", **REQUIRED)

    assert result["output"] == str((tmp_path / "out.jsonld").resolve())
    assert Path(result["output"]).is_file()
    assert Path(result["report_path"]).is_absolute()


def test_bake_passes_every_optional_argument_to_the_generator(
    tmp_path: Path,
) -> None:
    """Each optional argument changes the document, so dropping one fails here."""
    data = tmp_path / "relational"
    data.mkdir()
    (data / "studies.csv").write_text("study_id,title\n1,Alpha\n2,Beta\n")
    (data / "samples.csv").write_text("sample_id,study_id,value\n10,1,0.5\n")
    (data / "scratch.csv").write_text("a\n1\n")
    (data / "notes.txt").write_text("not data\n")
    output = tmp_path / "out.jsonld"

    mcp_server.bake(
        input_dir=str(data),
        output=str(output),
        url="https://example.org/rel",
        citation="Doe J. Two related tables. 2024.",
        date_published="2024-01-02",
        detect_references=True,
        include=["*.csv"],
        exclude=["scratch.csv"],
        **REQUIRED,
    )

    document = json.loads(output.read_text(encoding="utf-8"))
    names = sorted(d["name"] for d in document["distribution"])
    fields = {
        rs["@id"]: {f["name"]: f for f in rs["field"]} for rs in document["recordSet"]
    }
    assert document["url"] == "https://example.org/rel"
    assert document["citeAs"] == "Doe J. Two related tables. 2024."
    assert document["datePublished"].startswith("2024-01-02")
    assert names == ["samples.csv", "studies.csv"]
    assert fields["samples"]["study_id"]["references"] == {
        "field": {"@id": "studies/study_id"}
    }


def test_bake_writes_the_same_bytes_as_the_cli(dataset: Path, tmp_path: Path) -> None:
    """The server is a second front door to one pipeline, not a second pipeline.

    Same fixture, same fields, both ways: the documents and the per-file
    reports must match byte for byte.
    """
    from typer.testing import CliRunner

    from croissant_baker.__main__ import app

    fields = {
        "name": "gharchive-demo",
        "description": "A committed subset of the GH Archive.",
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "url": "https://example.org/gharchive",
        "citation": "GH Archive.",
        "date_published": "2024-01-02",
    }
    cli_output = tmp_path / "cli" / "out.jsonld"
    cli_report = tmp_path / "cli" / "out.report.json"
    mcp_output = tmp_path / "mcp" / "out.jsonld"

    result = CliRunner().invoke(
        app,
        [
            "--input",
            str(dataset),
            "--output",
            str(cli_output),
            "--report",
            str(cli_report),
            "--creator",
            "Jane Doe,jane@example.com",
            *(
                arg
                for key, value in fields.items()
                for arg in (f"--{key.replace('_', '-')}", value)
            ),
        ],
    )
    assert result.exit_code == 0, result.output

    baked = mcp_server.bake(
        input_dir=str(dataset),
        output=str(mcp_output),
        creators=["Jane Doe,jane@example.com"],
        **fields,
    )

    assert mcp_output.read_bytes() == cli_output.read_bytes()
    assert Path(baked["report_path"]).read_bytes() == cli_report.read_bytes()


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

    assert "creators" in str(refusal.value)
    assert "--creator" not in str(refusal.value), "a CLI flag means nothing here"
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

    with pytest.raises(ToolError, match="At least one") as refusal:
        asyncio.run(mcp_server.build_server().call_tool("bake", arguments))

    assert "creators" in str(refusal.value)
    assert "--creator" not in str(refusal.value), "a CLI flag means nothing here"

    assert not output.exists()


def test_bake_refuses_a_date_that_is_not_iso(dataset: Path, tmp_path: Path) -> None:
    """A bad date reaches the client as the refusal, and nothing is written."""
    from mcp.server.mcpserver.exceptions import ToolError

    output = tmp_path / "out.jsonld"
    arguments = {
        "input_dir": str(dataset),
        "output": str(output),
        "date_published": "last spring",
        **REQUIRED,
    }

    with pytest.raises(ToolError, match="Invalid date format") as refusal:
        asyncio.run(mcp_server.build_server().call_tool("bake", arguments))

    assert "date_published" in str(refusal.value)
    assert "--date-published" not in str(refusal.value)

    assert not output.exists()


def test_validate_reports_the_error_on_broken_jsonld(tmp_path: Path) -> None:
    """A document mlcroissant cannot construct comes back with the error text."""
    broken = tmp_path / "broken.jsonld"
    broken.write_text(json.dumps({"@type": "sc:Dataset"}), encoding="utf-8")

    result = mcp_server.validate(str(broken))

    assert result["valid"] is False
    assert isinstance(result["error"], str) and result["error"]


def _call_validate(path: str):
    return asyncio.run(mcp_server.build_server().call_tool("validate", {"path": path}))


def test_validate_on_a_missing_file_is_an_error_not_an_invalid_document(
    tmp_path: Path,
) -> None:
    """``valid: False`` means mlcroissant refused a document that exists.

    A path that is not there is a different failure: an agent branching on
    ``valid`` would otherwise try to repair a document that was never written.
    """
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="missing.jsonld"):
        _call_validate(str(tmp_path / "missing.jsonld"))


def test_validate_on_an_unreadable_file_is_an_error(tmp_path: Path) -> None:
    """A file the server cannot open is the caller's path problem, too."""
    from mcp.server.mcpserver.exceptions import ToolError

    locked = tmp_path / "locked.jsonld"
    locked.write_text("{}", encoding="utf-8")
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running with privileges that ignore file modes")
        with pytest.raises(ToolError, match="locked.jsonld"):
            _call_validate(str(locked))
    finally:
        locked.chmod(0o600)


@pytest.mark.parametrize(
    "path",
    ["https://example.org/croissant.jsonld", "http://127.0.0.1:9/x.jsonld"],
)
def test_validate_never_fetches_a_url(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mlcroissant fetches a URL it is given; the server must not pass one on.

    A URL smuggled into a prompt would otherwise become a request from the
    user's machine, and the server promises it makes none.
    """
    import mlcroissant as mlc
    from mcp.server.mcpserver.exceptions import ToolError

    def refuse(*args, **kwargs):
        raise AssertionError("mlcroissant was asked to load a URL")

    monkeypatch.setattr(mlc, "Dataset", refuse)

    with pytest.raises(ToolError, match="local file"):
        _call_validate(path)


def test_validate_hands_mlcroissant_the_resolved_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mlcroissant gets the file that was checked, not the raw string again."""
    import mlcroissant as mlc

    document = tmp_path / "doc.jsonld"
    document.write_text("{}", encoding="utf-8")
    seen = []
    monkeypatch.setattr(mlc, "Dataset", lambda path: seen.append(path))
    monkeypatch.chdir(tmp_path)

    assert mcp_server.validate("doc.jsonld") == {"valid": True}
    assert seen == [str(document.resolve())]


def test_build_server_registers_exactly_the_three_tools() -> None:
    """The surface is deliberately narrow: three tools, no more."""
    tools = asyncio.run(mcp_server.build_server().list_tools())

    assert sorted(tool.name for tool in tools) == ["bake", "dry_run", "validate"]


def test_tools_tell_the_client_which_are_read_only() -> None:
    """A client can auto approve the two read-only tools and ask about bake.

    Without the hints a client must treat all three alike, and bake writes the
    output, the report and any missing parent directories.
    """
    tools = {
        tool.name: tool.annotations
        for tool in asyncio.run(mcp_server.build_server().list_tools())
    }

    assert tools["dry_run"].read_only_hint is True
    assert tools["validate"].read_only_hint is True
    assert tools["bake"].read_only_hint is False
    assert tools["bake"].destructive_hint is True
    assert all(hints.open_world_hint is False for hints in tools.values()), (
        "no tool reaches beyond the local machine"
    )


def test_the_server_reports_the_package_version() -> None:
    """The client sees which croissant-baker it is talking to."""
    from croissant_baker.pipeline import get_version

    assert mcp_server.build_server().version == get_version() != ""


def test_the_server_publishes_the_skill_and_the_rai_template() -> None:
    """An agent that connects can read both without a filesystem path.

    The skill sends the agent to the RAI template, so a client that reached the
    skill over MCP has to be able to reach the template the same way.
    """
    resources = asyncio.run(mcp_server.build_server().list_resources())

    assert {str(r.uri): r.mime_type for r in resources} == {
        "croissant-baker://skill": "text/markdown",
        "croissant-baker://rai-template": "application/yaml",
    }


@pytest.mark.parametrize(
    "uri, relative",
    [
        ("croissant-baker://skill", "SKILL.md"),
        ("croissant-baker://rai-template", "assets/rai-template.yaml"),
    ],
)
def test_each_resource_serves_the_packaged_file(uri: str, relative: str) -> None:
    """A resource serves the file that ships in the package, verbatim."""
    packaged = (
        importlib.resources.files("croissant_baker")
        .joinpath("skills", "croissant-baker", relative)
        .read_text(encoding="utf-8")
    )

    contents = asyncio.run(mcp_server.build_server().read_resource(uri))

    assert "".join(chunk.content for chunk in contents) == packaged


def test_every_resource_the_skill_names_is_served() -> None:
    """A URI the skill points at and the server does not serve is a dead end."""
    named = set(re.findall(r"croissant-baker://[a-z-]+", mcp_server.skill_markdown()))
    served = {
        str(r.uri) for r in asyncio.run(mcp_server.build_server().list_resources())
    }

    assert named and named <= served


def test_dry_run_on_a_missing_directory_names_it_to_the_client(tmp_path: Path) -> None:
    """A missing input directory reaches the client with its path, as in bake."""
    from mcp.server.mcpserver.exceptions import ToolError

    missing = tmp_path / "no-such-dir"

    with pytest.raises(ToolError, match="no-such-dir"):
        asyncio.run(
            mcp_server.build_server().call_tool("dry_run", {"input_dir": str(missing)})
        )
