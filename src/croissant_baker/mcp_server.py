"""A local stdio MCP server: a second front door to the same pipeline.

The three tools below are plain functions with typed signatures, independent of
any transport, so they can be called directly by tests and by other Python
code. :func:`build_server` is the only place that knows about the MCP SDK, and
it is imported lazily so the package stays installable without the optional
``mcp`` dependency group.

The surface is deliberately narrow. ``bake`` accepts the semantic fields a
human would type and nothing else: an agent can supply only what a person
could, the structural layer is untouched, and the ``ScanReport`` comes back
verbatim so every refusal and its reason are visible. There is no fetch, search
or upload tool, and no HTTP transport, so the local-first invariant holds.

Alongside the tools the server publishes one read-only resource: the packaged
Agent Skill. A client that has the tools but not the skill would otherwise have
to be told separately how to use them.
"""

from __future__ import annotations

import importlib.resources
from collections import Counter
from typing import Any, List, Optional

from croissant_baker.__main__ import _dry_run_entries, _parse_creators, _save_dict
from croissant_baker.metadata_generator import MetadataGenerator
from croissant_baker.report import ScanReport
from croissant_baker.scan import Outcome, Reason

#: The name the server reports to a connecting client.
SERVER_NAME = "croissant-baker"

#: URI of the bundled Agent Skill, served as the server's one resource.
SKILL_URI = "croissant-baker://skill"


def skill_markdown() -> str:
    """Return the text of the bundled ``SKILL.md``.

    Read through :mod:`importlib.resources` rather than from a path relative
    to this file, so it resolves the same way from a wheel, a zip import and a
    source checkout. Nothing is read until a client asks for the resource.
    """
    return (
        importlib.resources.files("croissant_baker")
        .joinpath("skills", "croissant-baker", "SKILL.md")
        .read_text(encoding="utf-8")
    )


def dry_run(
    input_dir: str,
    include: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
) -> dict:
    """Report what a bake of ``input_dir`` would describe, without reading files.

    The counters are the dry run's own, not :meth:`ScanReport.to_dict`'s. That
    summary is written for a completed bake, where a file is either in the
    document or accounted for by a reason; a dry run reads nothing, so every
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
    entries = _dry_run_entries(input_dir, include, exclude)
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
        detect_references: Detect foreign keys between record sets.
        include: Optional glob patterns; only matching files are described.
        exclude: Optional glob patterns; matching files are skipped.

    Returns:
        ``{"output": <path written>, "report": <scan report>}``. The report is
        the completed bake's :meth:`ScanReport.to_dict`, so it counts what the
        document carries rather than what a dry run predicted.

    Raises:
        ValueError: If the document fails ``mlcroissant`` validation, in which
            case nothing is written.
    """
    generator = MetadataGenerator(
        dataset_path=input_dir,
        name=name,
        description=description,
        url=url,
        license=license,
        citation=citation,
        creators=_parse_creators(creators) or None,
        detect_references=detect_references,
        includes=include,
        excludes=exclude,
    )
    metadata_dict = generator.generate_metadata()
    _save_dict(metadata_dict, output, validate=True)
    return {"output": output, "report": generator.scan_report.to_dict()}


def validate(path: str) -> dict:
    """Check that a Croissant file constructs under ``mlcroissant``.

    Args:
        path: Path to a Croissant JSON-LD file.

    Returns:
        ``{"valid": True}``, or ``{"valid": False, "error": <message>}``.
    """
    import mlcroissant as mlc

    try:
        mlc.Dataset(path)
    except Exception as exc:
        return {"valid": False, "error": str(exc)}
    return {"valid": True}


def build_server() -> Any:
    """Build the MCP server with the three tools and the skill resource.

    Returns:
        An ``MCPServer`` from the ``mcp`` package, imported here so the
        dependency stays optional.

    Raises:
        ImportError: If the optional ``mcp`` dependency group is not installed.
    """
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(SERVER_NAME)
    for tool in (dry_run, bake, validate):
        server.add_tool(tool)
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
    return server


def serve() -> None:
    """Run the server over stdio. The only transport this module offers."""
    build_server().run(transport="stdio")
