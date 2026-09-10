"""Whole-slide image handler: digital pathology in a vendor TIFF container.

The reading is elsewhere. :mod:`croissant_baker.handlers.wsi` knows the five
vendors and no Croissant, and this module knows Croissant and no vendor.
"""

import logging

from croissant_baker.handlers import wsi
from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.image_handler import _TIFF_MAGICS
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

#: Every vendor here writes a TIFF container and none has a media type of its
#: own registered, so naming one would be a statement no reader can act on.
MIME_TYPE = "image/tiff"

#: Enough bytes for the longest TIFF signature.
MAGIC_PREFIX_BYTES = 4


class WSIHandler(FileTypeHandler):
    """Handler for vendor whole-slide images.

    Aperio (``.svs``), Hamamatsu (``.ndpi``), Leica (``.scn``), Ventana
    (``.bif``) and Akoya (``.qptiff``). All five are TIFF containers, so the
    claim is the vendor extension over TIFF magic: the bytes of a pyramidal
    TIFF say nothing about whether the pyramid holds tissue, and ``.tif`` is
    left to :class:`~croissant_baker.handlers.image_handler.ImageHandler`.
    """

    EXTENSIONS = (".svs", ".ndpi", ".scn", ".bif", ".qptiff")
    FORMAT_NAME = "Whole-slide image"
    FORMAT_DESCRIPTION = (
        "Vendor, pyramid levels, tile size, microns per pixel, objective "
        "magnification and associated images for Aperio, Hamamatsu, Leica, "
        "Ventana and Akoya slides"
    )

    def claims(self, source: FileSource) -> bool:
        if source.suffix not in self.EXTENSIONS:
            return False
        # Both TIFF versions and both byte orders: a slide crosses the 4 GiB
        # that sends a writer to BigTIFF far more often than it does not.
        return source.peek(MAGIC_PREFIX_BYTES).startswith(_TIFF_MAGICS)

    def extract(self, source: FileSource, **kwargs) -> dict:
        import tifffile

        if not source.exists:
            raise FileNotFoundError(
                f"Whole-slide image not found: {source.relative_path}"
            )

        try:
            with source.open() as stream, tifffile.TiffFile(stream) as tif:
                header = wsi.read(tif)
        except Exception as e:
            raise ValueError(
                f"Failed to read whole-slide image {source.relative_path}: {e}"
            ) from e

        if header.refusal:
            # The count reaches the document through the record-set
            # description, which is where a described file's partial refusal
            # has to live: the scan report clears reason and detail once a file
            # is described. This names the individual file for an application
            # that configures logging; the package handler is a NullHandler, so
            # the library writes to no terminal of its own.
            logger.warning(
                "%s: the vendor slide description was not parsed (%s). The "
                "file is described from its TIFF tags.",
                source.relative_path,
                header.refusal,
            )

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": MIME_TYPE,
            "width": header.width,
            "height": header.height,
            "slide": header,
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        # An empty batch has nothing to summarise; emitting a FileSet over
        # zero files would describe data that is not there.
        if not file_metas:
            return BuildResult([], [])
        return BuildResult([], [])
