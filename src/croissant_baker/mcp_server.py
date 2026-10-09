"""A local stdio MCP server: a second front door to the same pipeline.

The three tools below are plain functions with typed signatures, independent of
any transport, so they can be called directly by tests and by other Python
code. :func:`build_server` is the only place that knows about the MCP SDK, and
it is imported lazily so the package stays installable without the optional
``mcp`` extra.

The surface is deliberately narrow. ``bake`` accepts the semantic fields a
human would type and nothing else: an agent can supply only what a person
could, and the structural layer is untouched. The ``ScanReport`` counters come
back with every refusal counted by reason, and the full per-file report is
written next to the output. There is no fetch, search or upload tool, and no
HTTP transport, so the local-first invariant holds.

Alongside the tools the server publishes two read-only resources: the packaged
Agent Skill and the RAI config template it points at. A client that has the
tools but not the skill would otherwise have to be told separately how to use
them, and could not open a file the skill names.
"""

from __future__ import annotations

import functools
import glob
import importlib.resources
from collections import Counter
from pathlib import Path
from typing import Any, Callable, List, Optional

from croissant_baker.metadata_generator import MetadataGenerator
from croissant_baker.pipeline import (
    dry_run_entries,
    get_version,
    parse_creators,
    save_dict,
    write_scan_report,
)
from croissant_baker.report import ScanReport
from croissant_baker.scan import Outcome, Reason

#: The name the server reports to a connecting client.
SERVER_NAME = "croissant-baker"

#: URI of the bundled Agent Skill, served as a resource.
SKILL_URI = "croissant-baker://skill"

#: URI of the RAI config template the skill points at, served as a resource.
RAI_TEMPLATE_URI = "croissant-baker://rai-template"


def _skill_file(*parts: str) -> str:
    """Return the text of a file in the bundled skill directory.

    Read through :mod:`importlib.resources` rather than from a path relative
    to this file, so it resolves the same way from a wheel, a zip import and a
    source checkout. Nothing is read until a client asks for the resource.
    """
    return (
        importlib.resources.files("croissant_baker")
        .joinpath("skills", "croissant-baker", *parts)
        .read_text(encoding="utf-8")
    )


def skill_markdown() -> str:
    """Return the text of the bundled ``SKILL.md``."""
    return _skill_file("SKILL.md")


def rai_template_yaml() -> str:
    """Return the text of the bundled ``assets/rai-template.yaml``."""
    return _skill_file("assets", "rai-template.yaml")


def dry_run(
    input_dir: str,
    include: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
) -> dict:
    """Report what a bake of ``input_dir`` would describe, without baking it.

    The counters are the dry run's own, not :meth:`ScanReport.to_dict`'s. That
    summary is written for a completed bake, where a file is either in the
    document or accounted for by a reason; a dry run describes nothing, so every
    file would land in ``undescribed`` and a directory that bakes cleanly would
    report as describing none of it. The counts here mirror what the CLI's
    ``--dry-run`` prints: claimed and unclaimed.

    Args:
        input_dir: Directory containing the dataset files.
        include: Optional glob patterns; only matching files are scanned.
        exclude: Optional glob patterns; matching files are skipped.

    Returns:
        ``total``, ``would_process`` and ``unclaimed`` counts, ``by_reason``
        accounting for the unclaimed alone, and ``files``: the per-file outcome
        with the reason and a human-readable detail for every refusal.
    """
    entries = dry_run_entries(input_dir, include, exclude)
    claimed = [e for e in entries if e.outcome is Outcome.WOULD_PROCESS]
    unclaimed = [e for e in entries if e.outcome is Outcome.UNCLAIMED]
    tally = Counter(e.reason for e in unclaimed if e.reason is not None)
    return {
        "total": len(entries),
        "would_process": len(claimed),
        "unclaimed": len(unclaimed),
        "by_reason": {r.value: tally[r] for r in Reason if tally[r]},
        "files": ScanReport(entries).to_dict()["files"],
    }


def bake(
    input_dir: str,
    output: str,
    name: str,
    description: str,
    license: str,
    creators: List[str],
    url: Optional[str] = None,
    citation: Optional[str] = None,
    date_published: Optional[str] = None,
    detect_references: bool = False,
    include: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
) -> dict:
    """Generate Croissant metadata for ``input_dir`` and write it to ``output``.

    Args:
        input_dir: Directory containing the dataset files.
        output: Path of the ``.jsonld`` file to write.
        name: Dataset name.
        description: Dataset description.
        license: Dataset license, preferably as a URL.
        creators: Creators, each ``"Name"``, ``"Name,email"`` or
            ``"Name,email,url"``, the same format the CLI's ``--creator``
            accepts.
        url: Optional dataset homepage.
        citation: Optional citation text.
        date_published: Optional publication date in ISO format, such as
            ``2024-01-02``. The spec expects one, and the CLI's
            ``--date-published`` is the same field.
        detect_references: Detect foreign keys between record sets.
        include: Optional glob patterns; only matching files are described.
        exclude: Optional glob patterns; matching files are skipped.

    Returns:
        ``output`` and ``report_path``, both absolute, then the completed
        bake's counters from :meth:`ScanReport.to_dict`: ``total``,
        ``described``, ``linked``, ``referenced``, ``undescribed``,
        ``by_reason`` and ``by_diagnostic``. The per-file list is left out,
        since a large tree would flood the caller with one entry per file, as
        the CLI's bounded summary avoids too. It is written instead to
        ``report_path``, next to the output: ``out.jsonld`` gets
        ``out.report.json``. A relative ``output`` resolves against the
        server's working directory, which the caller may not know, so the
        paths returned are the ones really written. When either file falls
        inside ``input_dir`` it is left out of the scan, so the next bake
        does not describe it.

    Raises:
        ValueError: If there is no creator, a creator has a blank name part,
            ``date_published`` is not an ISO date, or the document fails
            ``mlcroissant`` validation; in each case nothing is written.
    """
    output_path = Path(output).resolve()
    report_path = output_path.with_suffix(".report.json")
    own_files = _own_files_under(input_dir, output_path, report_path)

    generator = MetadataGenerator(
        dataset_path=input_dir,
        name=name,
        description=description,
        url=url,
        license=license,
        citation=citation,
        date_published=date_published,
        creators=parse_creators(creators),
        detect_references=detect_references,
        includes=include,
        excludes=[*(exclude or []), *own_files] or None,
    )
    metadata_dict = generator.generate_metadata()
    save_dict(metadata_dict, str(output_path), validate=True)
    write_scan_report(generator.scan_report, report_path)
    summary = generator.scan_report.to_dict()
    del summary["files"]
    return {"output": str(output_path), "report_path": str(report_path), **summary}


def _own_files_under(input_dir: str, *paths: Path) -> List[str]:
    """Exclude patterns for the files a bake writes inside its own input.

    A bake written into the dataset directory would otherwise describe its
    own output and report on the next run, and the report, being JSON, is
    claimed and refused. Each pattern is the file's escaped relative path,
    and a glob matches from the right, so a file of the same name deeper in
    the tree is skipped too: that is another bake's output.
    """
    root = Path(input_dir).resolve()
    return [
        glob.escape(path.relative_to(root).as_posix())
        for path in paths
        if path.is_relative_to(root)
    ]


def validate(path: str) -> dict:
    """Check that a Croissant file constructs under ``mlcroissant``.

    Only a file on this machine is read. ``mlcroissant`` would fetch a URL
    it is given, and a URL that reached this tool from untrusted text would
    then become a request from the user's machine.

    Args:
        path: Path to a local Croissant JSON-LD file.

    Returns:
        ``{"valid": True}``, or ``{"valid": False, "error": <message>}`` when
        ``mlcroissant`` read the file and refused it.

    Raises:
        FileNotFoundError: If ``path`` is not a local file, including a URL.
            Kept apart from ``valid: False``, which is about a document that
            exists.
        OSError: If the file cannot be opened.
    """
    import mlcroissant as mlc

    if not Path(path).is_file():
        raise FileNotFoundError(
            f"{path} is not a local file. validate reads files on this "
            "machine only, and never fetches a URL."
        )
    with open(path, "rb"):
        pass

    try:
        mlc.Dataset(path)
    except Exception as exc:
        return {"valid": False, "error": str(exc)}
    return {"valid": True}


def _reported_to_client(tool: Callable[..., Any], error: type) -> Callable[..., Any]:
    """Wrap ``tool`` so a refusal it raises reaches the client as ``error``.

    The SDK treats any other exception from a tool as a crash and hides its
    text, so the client would learn only that the tool failed. A ``ValueError``
    here is a refusal of the caller's input, such as a creator with no name,
    and an ``OSError`` is a path the caller named that cannot be read or
    written; either message says what to fix.
    """

    @functools.wraps(tool)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return tool(*args, **kwargs)
        except (ValueError, OSError) as exc:
            raise error(str(exc)) from exc

    return wrapper


def build_server() -> Any:
    """Build the MCP server with the three tools and the two resources.

    Returns:
        An ``MCPServer`` from the ``mcp`` package, imported here so the
        dependency stays optional.

    Raises:
        ImportError: If the optional ``mcp`` extra is not installed.
    """
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    # The hints let a client approve the two tools that only read on its own
    # and ask before bake, which writes the output, the report and any
    # missing parent directories, replacing files already there.
    read_only = ToolAnnotations(read_only_hint=True)
    writes = ToolAnnotations(read_only_hint=False, destructive_hint=True)

    server = MCPServer(SERVER_NAME, version=get_version())
    for tool, hints in (
        (dry_run, read_only),
        (bake, writes),
        (validate, read_only),
    ):
        server.add_tool(_reported_to_client(tool, ToolError), annotations=hints)
    server.resource(
        SKILL_URI,
        name="croissant-baker-skill",
        title="Croissant Baker Agent Skill",
        description=(
            "How to drive croissant-baker: the dry-run-bake-validate loop, "
            "which fields are inferred and which must be asked for, and the "
            "gotchas."
        ),
        mime_type="text/markdown",
    )(skill_markdown)
    server.resource(
        RAI_TEMPLATE_URI,
        name="croissant-baker-rai-template",
        title="Croissant Baker RAI config template",
        description=(
            "Starting point for --rai-config: every key the loader accepts, "
            "with a comment on what belongs in each."
        ),
        mime_type="application/yaml",
    )(rai_template_yaml)
    return server


def serve() -> None:
    """Run the server over stdio. The only transport this module offers."""
    build_server().run(transport="stdio")
