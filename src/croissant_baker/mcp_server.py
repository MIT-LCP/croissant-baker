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
"""

from __future__ import annotations

from typing import Any

from croissant_baker.__main__ import _dry_run_entries, _parse_creators, _save_dict
from croissant_baker.metadata_generator import MetadataGenerator
from croissant_baker.report import ScanReport

#: The name the server reports to a connecting client.
SERVER_NAME = "croissant-baker"


def dry_run(
    input_dir: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> dict:
    """Report what a bake of ``input_dir`` would describe, without reading files.

    Args:
        input_dir: Directory containing the dataset files.
        include: Optional glob patterns; only matching files are scanned.
        exclude: Optional glob patterns; matching files are skipped.

    Returns:
        A scan report: per-file outcome (``would_process`` or ``unclaimed``)
        with the reason and a human-readable detail for every refusal.
    """
    return ScanReport(_dry_run_entries(input_dir, include, exclude)).to_dict()


def bake(
    input_dir: str,
    output: str,
    name: str,
    description: str,
    license: str,
    creators: list[str],
    url: str | None = None,
    citation: str | None = None,
    detect_references: bool = False,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
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
        the same per-file account ``dry_run`` returns, resolved against what
        the bake actually described.

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
    """Build the MCP server with the three tools registered.

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
    return server


def serve() -> None:
    """Run the server over stdio. The only transport this module offers."""
    build_server().run(transport="stdio")
