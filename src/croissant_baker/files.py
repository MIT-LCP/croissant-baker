"""File discovery utilities for Croissant Maker."""

import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Cap on example paths retained for the hidden-directory skip debug log. The
# skipped count is always exact; only this example list is bounded, so a dataset
# with a huge hidden tree (an accidental .git or .ipynb_checkpoints, say) cannot
# accumulate an unbounded list of paths we only ever sample from.
_MAX_SKIPPED_EXAMPLES = 5


def discover_files(
    dir_path: str,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
) -> List[Path]:
    """
    Recursively discover all files in a directory (skipping hidden directories)
    and return their relative paths.

    Args:
        dir_path: Path to the directory to scan.
        include_patterns: Optional list of glob patterns to include.
        exclude_patterns: Optional list of glob patterns to exclude.

    Returns:
        List of relative file paths found in the directory.

    Raises:
        FileNotFoundError: If the directory does not exist or is not a directory.
        PermissionError: If the directory cannot be accessed.
    """
    try:
        directory = Path(dir_path).resolve()
        if not directory.is_dir():
            raise FileNotFoundError(f"{dir_path} is not a directory")

        skipped_count = 0
        skipped_examples: List[str] = []

        files = []
        for file in directory.rglob("*"):
            if not file.is_file():
                continue

            rel_path = file.relative_to(directory)

            if any(part.startswith(".") for part in rel_path.parts):
                skipped_count += 1
                if len(skipped_examples) < _MAX_SKIPPED_EXAMPLES:
                    skipped_examples.append(str(rel_path))
                continue

            files.append(rel_path)

        if skipped_count:
            logger.debug(
                "Skipping %d file(s) in hidden directories. Examples: %s",
                skipped_count,
                skipped_examples,
            )

        if include_patterns:
            files = [f for f in files if any(f.match(p) for p in include_patterns)]

        if exclude_patterns:
            files = [f for f in files if not any(f.match(p) for p in exclude_patterns)]

        return files
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Directory not found: {e}")
    except PermissionError as e:
        raise PermissionError(f"Permission denied: {e}")
