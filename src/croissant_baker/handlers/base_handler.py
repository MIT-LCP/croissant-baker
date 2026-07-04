"""Abstract base class for file type handlers."""

from abc import ABC, abstractmethod
from pathlib import Path


class FileTypeHandler(ABC):
    """
    Abstract base class for file type handlers.

    Each handler is responsible for three things:
    - can_handle: decide if this handler owns a given file
    - extract_metadata: extract raw metadata from a single file
    - build_croissant: turn that metadata into Croissant FileSets + RecordSets

    All three are required. The generator owns FileObject creation and ID
    assignment; build_croissant returns only FileSets and RecordSets.

    Adding a new format: subclass this, implement all three methods,
    register the instance in registry.py — no other files need to change.

    Subclasses should also set these class attributes for documentation:
    - EXTENSIONS: tuple of file extensions this handler claims (e.g. (".csv",))
    - FORMAT_NAME: short display name for docs (e.g. "CSV")
    - FORMAT_DESCRIPTION: one-line summary of what metadata is extracted
    """

    EXTENSIONS: tuple[str, ...] = ()
    FORMAT_NAME: str = ""
    FORMAT_DESCRIPTION: str = ""

    @abstractmethod
    def can_handle(self, file_path: Path) -> bool:
        """
        Check if the handler can process the given file.

        Args:
            file_path: Path to the file to check

        Returns:
            True if this handler can process the file, False otherwise
        """
        pass

    @abstractmethod
    def extract_metadata(self, file_path: Path, **kwargs: object) -> dict:
        """
        Extract comprehensive metadata from a single file.

        Thread-safety: ``extract_metadata`` may be called concurrently across
        files on a single shared handler instance — handlers are registered as
        singletons and extraction is parallelised (see ``MetadataGenerator``).
        Implementations must be safe to call concurrently: do not mutate shared
        or instance state during extraction, and use only parsers that are safe
        for concurrent reads of independent files. Read-only state set once at
        construction is fine; mutable per-call state must stay local — never on
        ``self`` (nor on the class or module).

        Should return a dictionary containing file information, structure,
        types, and any format-specific metadata needed for Croissant generation.

        Subclasses may declare additional named parameters before **kwargs
        to support handler-specific options (e.g. count_rows for CSV).

        Args:
            file_path: Path to the file to process
            **kwargs: Handler-specific options forwarded from MetadataGenerator

        Returns:
            Dictionary containing extracted metadata. For tabular data, should include:
            - column_types: Dict mapping column names to Croissant types
            - Basic file info (path, name, size, hash, encoding_format)

        Raises:
            Exception: If the file cannot be processed
        """
        pass

    @abstractmethod
    def build_croissant(
        self,
        file_metas: list[dict],
        file_ids: list[str],
    ) -> tuple[list, list]:  # (list[FileSet], list[RecordSet])
        """Build Croissant FileSets and RecordSets for all files this handler processed.

        Called once per handler after the FileObject loop. Receives all metadata
        dicts this handler produced for the dataset, with pre-assigned FileObject
        IDs aligned by position.

        Args:
            file_metas: metadata dicts from extract_metadata, one per file
            file_ids: FileObject IDs assigned by the generator, aligned with file_metas

        Returns:
            (additional_distributions, record_sets) — additional_distributions
            contains FileSets only. FileObjects are always owned by the generator.
        """
        pass
