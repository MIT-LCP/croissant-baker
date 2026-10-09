"""The steps the CLI and the MCP server share, so the two cannot disagree.

Both front doors parse creators, resolve a dry run and write the document
through the functions here. They live in their own module rather than in
``__main__`` because ``python -m croissant_baker`` runs that file as a script:
importing it again from the server would load a second copy of it.
"""

import csv
import importlib.metadata
import json
import tempfile
from pathlib import Path
from typing import List, Optional

import mlcroissant as mlc

from croissant_baker.handlers.registry import select_handler
from croissant_baker.metadata_generator import serialize_datetime
from croissant_baker.scan import Reason, ScanEntry, ScanReport, scan_directory


def save_dict(metadata_dict: dict, output_path: str, validate: bool) -> None:
    """
    Save a pre-computed metadata dict to a JSON-LD file, with optional validation.

    This function exists because MetadataGenerator.save_metadata() always calls
    generate_metadata() internally, regenerating the dict from scratch. That makes
    it unusable once the dict has already been built and modified — for example,
    after RAI attributes have been injected via inject_rai(). This function takes
    the already-computed dict and handles the save + validation step directly,
    keeping MetadataGenerator unchanged.

    It is used in three places:
      - The main generate command, when --rai-config is provided (or not, to keep
        a single consistent save path after generate_metadata() is called once).
      - The rai-apply command, which loads an existing .jsonld, injects RAI, and
        saves it back without invoking MetadataGenerator at all.
      - The MCP server's bake tool, so a document it writes is saved and
        validated exactly as the CLI's is.

    Args:
        metadata_dict: Already-computed Croissant metadata dict (may include RAI).
        output_path:   Path where the JSON-LD file should be written.
        validate:      When True, validates via mlcroissant before writing.

    Raises:
        ValueError: If mlcroissant validation fails.
    """
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if validate:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonld", delete=False
        ) as tmp:
            json.dump(
                metadata_dict,
                tmp,
                indent=2,
                ensure_ascii=False,
                default=serialize_datetime,
            )
            tmp_path = tmp.name
        try:
            mlc.Dataset(tmp_path)
            write_jsonld(metadata_dict, output_file)
        except Exception as e:
            raise ValueError(f"Validation failed: {e}")
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    else:
        write_jsonld(metadata_dict, output_file)


def write_jsonld(metadata_dict: dict, output_file: Path) -> None:
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            metadata_dict, f, indent=2, ensure_ascii=False, default=serialize_datetime
        )
        f.write("\n")


def write_scan_report(scan_report: ScanReport, path: Path) -> None:
    """Write the machine-readable scan report as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(scan_report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def get_version() -> str:
    """Get version from package metadata."""
    try:
        return importlib.metadata.version("croissant-baker")
    except importlib.metadata.PackageNotFoundError:
        return "unknown (not installed as package)"


def parse_creators(creator: Optional[List[str]], cli: bool = True) -> List[dict]:
    """Parse ``Name,email,url`` creator strings into mlcroissant Person dicts.

    Semicolons take precedence as the separator so a name containing a comma
    needs no quoting; otherwise the string is read as one CSV row, which
    handles quoting properly.

    Args:
        creator: The creator strings, as typed.
        cli: Word a refusal for the CLI's ``--creator`` flag. False words it
            for the MCP server's ``creators`` argument, since a flag means
            nothing to a caller that never typed one.

    Raises:
        ValueError: If there is no creator at all, or an entry has a blank
            name part. The spec requires a creator, and without one the
            generator would fill in a placeholder person.
    """
    if cli:
        example = (
            "Example: --creator 'John Doe,john@example.com' or --creator 'Jane Smith'"
        )
        required = "At least one '--creator' option is required"
    else:
        example = 'Example: creators=["John Doe,john@example.com"]'
        required = "At least one entry in 'creators' is required"

    if not creator:
        raise ValueError(
            f"{required} to comply with the Croissant specification.\n{example}"
        )

    parsed_creators: List[dict] = []
    for raw_creator in creator:
        creator_info = raw_creator.strip()

        # Preferred: semicolon
        if ";" in creator_info:
            creator_parts = [p.strip() for p in creator_info.split(";")]

        else:
            # Use CSV parsing for comma cases (handles quotes properly)
            creator_parts = next(csv.reader([creator_info]))
            creator_parts = [p.strip() for p in creator_parts]

        # Skipping it would drop a creator the user asked for, or
        # leave the placeholder, without a word; refuse it instead.
        if not creator_parts or not creator_parts[0]:
            label = "--creator" if cli else "creators entry"
            raise ValueError(f"{label} {raw_creator!r} has no name.\n{example}")

        creator_obj = {"name": creator_parts[0]}

        if len(creator_parts) > 1 and creator_parts[1]:
            creator_obj["email"] = creator_parts[1]

        if len(creator_parts) > 2 and creator_parts[2]:
            creator_obj["url"] = creator_parts[2]

        parsed_creators.append(creator_obj)

    return parsed_creators


def dry_run_entries(
    input_dir: str,
    include: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
) -> List[ScanEntry]:
    """Resolve every discovered file to a handler, reading at most a header.

    Each entry comes back either ``WOULD_PROCESS`` or ``UNCLAIMED`` with the
    registry's own reason: an archive and a path-only handler differ, so the
    reason is asked for rather than assumed.
    """
    entries = scan_directory(
        input_dir, include_patterns=include, exclude_patterns=exclude
    )
    for entry in entries:
        selection = select_handler(Path(input_dir) / entry.path, entry.path)
        if selection.handler is None:
            entry.unclaimed(selection.reason or Reason.NO_HANDLER, selection.refusal)
        else:
            entry.would_process(selection.handler)
    return entries
