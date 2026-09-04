"""Which handlers exist, in which order, and which one owns a given file."""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from croissant_baker import compression
from croissant_baker.handlers.base_handler import (
    FileTypeHandler,
    InputKind,
    uses_legacy_claims,
    uses_legacy_extract,
    warn_legacy_once,
)
from croissant_baker.scan import Reason
from croissant_baker.sources import FileSource, make_source


@dataclass(frozen=True)
class HandlerSelection:
    """The outcome of asking the registry who owns a file.

    When nothing claimed it, ``reason`` is the category the scan summary counts
    and ``refusal`` is the sentence a human reads.
    """

    handler: Optional[FileTypeHandler] = None
    source: Optional[FileSource] = None
    reason: Optional[Reason] = None
    refusal: str = ""


# Routed per method, not per handler, so a handler that migrated only one of
# the two still works.


def claims(handler: FileTypeHandler, source: FileSource, real_path: Path) -> bool:
    """Ask one handler whether it describes ``source``."""
    if uses_legacy_claims(handler):
        warn_legacy_once(handler)
        return handler.can_handle(real_path)
    return handler.claims(source)


def extract(
    handler: FileTypeHandler, source: FileSource, real_path: Path, **kwargs
) -> dict:
    """Ask one handler to describe ``source``."""
    if uses_legacy_extract(handler):
        warn_legacy_once(handler)
        return handler.extract_metadata(real_path, **kwargs)
    return handler.extract(source, **kwargs)


class HandlerRegistry:
    """The handlers to consult, in the order to consult them.

    Order is registration order, and it decides overlapping claims.
    """

    def __init__(self, handlers: Optional[Iterable[FileTypeHandler]] = None):
        self._handlers: List[FileTypeHandler] = []
        for handler in handlers or ():
            self.register(handler)

    def __iter__(self):
        return iter(self._handlers)

    def __len__(self) -> int:
        return len(self._handlers)

    def register(self, handler: FileTypeHandler) -> None:
        """Add ``handler``, unless one of its class is already registered.

        Identity is the class, not the instance: callers construct a fresh
        instance each time, so deduplicating by instance would not deduplicate.
        """
        if any(type(h) is type(handler) for h in self._handlers):
            return
        self._handlers.append(handler)

    def handlers(self) -> List[FileTypeHandler]:
        """Every registered handler, in dispatch order."""
        return list(self._handlers)

    def select(
        self, file_path: Path, relative_path: Optional[Path] = None
    ) -> HandlerSelection:
        """Find the handler that owns ``file_path``, or say why none does.

        Every handler is asked about the logical file through a plain
        :class:`~croissant_baker.sources.FileSource`. Only a winner declaring
        ``InputKind.PATH`` gets a
        :class:`~croissant_baker.sources.PathSource`, and only for an
        uncompressed file; a compressed one is refused with its own reason.

        Args:
            file_path: Path to the file, wrapper suffix included.
            relative_path: Its path relative to the dataset root, for the source.
        """
        wrapper = compression.compression_for(file_path.name)
        source = make_source(file_path, relative_path)
        refused_for_input: Optional[FileTypeHandler] = None

        for handler in self._handlers:
            if not claims(handler, source, file_path):
                continue
            if handler.INPUT_KIND is not InputKind.PATH:
                return HandlerSelection(handler=handler, source=source)
            if wrapper is not None:
                # Another handler may still take it as a stream; this refusal
                # is only used if none does.
                refused_for_input = refused_for_input or handler
                continue
            return HandlerSelection(
                handler=handler,
                source=make_source(file_path, relative_path, with_path=True),
            )

        if refused_for_input is not None:
            name = refused_for_input.FORMAT_NAME or type(refused_for_input).__name__
            return HandlerSelection(
                reason=Reason.UNSUPPORTED_INPUT,
                refusal=(
                    f"{name} needs the file on disk and cannot read it through "
                    f"{wrapper.name} compression"
                ),
            )
        if compression.is_archive(file_path.name):
            return HandlerSelection(
                reason=Reason.ARCHIVE,
                refusal="archive; the baker does not open archives",
            )
        return HandlerSelection(
            reason=Reason.NO_HANDLER,
            refusal="no handler for file type",
        )


def builtin_handlers() -> List[FileTypeHandler]:
    """The handlers the baker ships with, in dispatch order."""
    # Imported here so that importing the registry does not pull in pydicom,
    # nibabel and Pillow.
    from croissant_baker.handlers.csv_handler import CSVHandler
    from croissant_baker.handlers.tsv_handler import TSVHandler
    from croissant_baker.handlers.fhir_handler import FHIRHandler
    from croissant_baker.handlers.json_handler import JSONHandler
    from croissant_baker.handlers.wfdb_handler import WFDBHandler
    from croissant_baker.handlers.parquet_handler import ParquetHandler
    from croissant_baker.handlers.image_handler import ImageHandler
    from croissant_baker.handlers.dicom_handler import DICOMHandler
    from croissant_baker.handlers.nifti_handler import NIfTIHandler
    from croissant_baker.handlers.soft_handler import SOFTHandler

    return [
        CSVHandler(),
        TSVHandler(),
        # FHIR first, as the narrower claim. Both sniff the content, so neither
        # order misroutes; this is convention, not a dependency.
        FHIRHandler(),
        JSONHandler(),
        WFDBHandler(),
        ParquetHandler(),
        ImageHandler(),
        DICOMHandler(),
        NIfTIHandler(),
        SOFTHandler(),
    ]


_default = HandlerRegistry()


def default_registry() -> HandlerRegistry:
    """The registry used unless a caller supplies its own.

    Filled on first use rather than at import, so importing the package does
    not construct nine handlers as a side effect.
    """
    if not _default:
        for handler in builtin_handlers():
            _default.register(handler)
    return _default


def register_handler(handler: FileTypeHandler) -> None:
    """Register a file type handler in the default registry."""
    default_registry().register(handler)


def select_handler(
    file_path: Path, relative_path: Optional[Path] = None
) -> HandlerSelection:
    """Ask the default registry who owns ``file_path``.

    See :meth:`HandlerRegistry.select`.
    """
    return default_registry().select(file_path, relative_path)


def find_handler(file_path: Path) -> Optional[FileTypeHandler]:
    """The first registered handler that can process ``file_path``, or None.

    Kept at this exact shape because external callers depend on it.
    """
    return select_handler(file_path).handler


def get_registered_handlers() -> List[FileTypeHandler]:
    """Every handler in the default registry."""
    return default_registry().handlers()


def register_all_handlers() -> None:
    """Idempotent, and no longer required: the default registry fills itself on
    first use. Kept because external callers still call it."""
    default_registry()
