"""Whole-slide image handler: digital pathology in a vendor TIFF container.

The reading is elsewhere. :mod:`croissant_baker.handlers.wsi` knows the five
vendors and no Croissant, and this module knows Croissant and no vendor.
"""

import logging
from pathlib import Path
from typing import Dict, List

import mlcroissant as mlc

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

FILE_SET_ID = "wsi-files"
RECORD_SET_ID = "slides"

#: What a slide with no vendor signature is counted as in a breakdown. It is
#: a TIFF that no scanner claimed, not a vendor named "unknown".
UNSIGNED = "no vendor signature"

#: Field name, Croissant type, description prefix, and
#: :class:`~croissant_baker.handlers.wsi.SlideHeader` attribute. A field is
#: emitted only where the batch has an observed value, so a batch of Leica
#: slides carries no ``mpp_x`` field describing a measurement none of them
#: made.
_SLIDE_FIELDS = (
    ("vendor", "sc:Text", "The scanner vendor that wrote the file", "vendor"),
    ("width", "sc:Integer", "Base level width in pixels", "width"),
    ("height", "sc:Integer", "Base level height in pixels", "height"),
    (
        "level_count",
        "sc:Integer",
        "Pyramid levels, the base level included",
        "level_count",
    ),
    ("mpp_x", "sc:Float", "Micrometres per pixel across the base level", "mpp_x"),
    ("mpp_y", "sc:Float", "Micrometres per pixel down the base level", "mpp_y"),
    (
        "objective_power",
        "sc:Float",
        "Magnification of the objective the slide was scanned through",
        "objective_power",
    ),
)


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
        """One FileSet over the slides, and one row per slide.

        Every vendor here describes one thing, a pyramid of a single tissue
        section, so unlike the OME split in the image handler there is no
        second collection: a Leica row simply leaves the pixel size unstated.
        """
        # An empty batch has nothing to summarise; emitting a FileSet over
        # zero files would describe data that is not there.
        if not file_metas:
            return BuildResult([], [])
        return BuildResult([_file_set(file_metas)], [_record_set(file_metas)])


def _headers(file_metas: List[Dict]) -> List[wsi.SlideHeader]:
    return [meta["slide"] for meta in file_metas]


def _includes(file_metas: List[Dict]) -> List[str]:
    """One glob per extension the batch actually holds.

    Both glob forms per extension: mlcroissant matches with fnmatch, where
    ``**/`` requires a directory, and slides sit at the dataset root as often
    as in a subdirectory.
    """
    patterns: Dict[str, str] = {}
    for meta in file_metas:
        extension = Path(meta["file_name"]).suffix
        lower = extension.lower()
        if extension != lower:
            # Globs are case-sensitive on Linux. One character-class pattern
            # covers every observed spelling without overlapping includes.
            spelling = "".join(
                f"[{char}{char.upper()}]" if char.isalpha() else char for char in lower
            )
            patterns[lower] = f"**/*{spelling}"
        else:
            patterns.setdefault(lower, f"**/*{lower}")
    return sorted(
        glob for pattern in patterns.values() for glob in (pattern, pattern[3:])
    )


def _file_set(file_metas: List[Dict]) -> mlc.FileSet:
    return mlc.FileSet(
        id=FILE_SET_ID,
        name="Whole-slide image files",
        description=(
            f"{len(file_metas)} whole-slide image file(s) ({_vendors(file_metas)})"
        ),
        encoding_formats=sorted({meta["encoding_format"] for meta in file_metas}),
        includes=_includes(file_metas),
    )


def _record_set(file_metas: List[Dict]) -> mlc.RecordSet:
    fields = [
        mlc.Field(
            id=f"{RECORD_SET_ID}/image",
            name="image",
            description=f"Slide content ({len(file_metas)} whole-slide file(s))",
            data_types=["sc:ImageObject"],
            source=mlc.Source(
                file_set=FILE_SET_ID,
                extract=mlc.Extract(file_property="content"),
            ),
        ),
        mlc.Field(
            id=f"{RECORD_SET_ID}/filename",
            name="filename",
            description="The slide's file name, which identifies the section",
            data_types=["sc:Text"],
            source=mlc.Source(
                file_set=FILE_SET_ID,
                extract=mlc.Extract(file_property="filename"),
            ),
        ),
    ]

    headers = _headers(file_metas)
    for name, data_type, prefix, attribute in _SLIDE_FIELDS:
        values = [
            value
            for value in (getattr(header, attribute) for header in headers)
            if value is not None and value != ""
        ]
        if not values:
            continue
        fields.append(
            mlc.Field(
                id=f"{RECORD_SET_ID}/{name}",
                name=name,
                description=f"{prefix} ({_observed(values)})",
                data_types=[data_type],
                source=mlc.Source(
                    file_set=FILE_SET_ID,
                    extract=mlc.Extract(file_property="content"),
                ),
            )
        )

    return mlc.RecordSet(
        id=RECORD_SET_ID,
        name=RECORD_SET_ID,
        description=_description(file_metas),
        fields=fields,
    )


def _description(file_metas: List[Dict]) -> str:
    """What the batch holds, in the terms a pathologist would ask about it."""
    headers = _headers(file_metas)
    total = len(file_metas)
    magnifications = [
        header.objective_power
        for header in headers
        if header.objective_power is not None
    ]
    text = (
        f"{total} whole-slide image(s) ({_dimensions(headers)}): "
        f"{_vendors(file_metas)}. Objective magnification: "
        f"{_observed(magnifications) if magnifications else 'not stated'}."
    )

    refused = [header.refusal for header in headers if header.refusal]
    if refused:
        text += (
            f" {len(refused)} of {total} carried a vendor slide description "
            f"that was not parsed: {'; '.join(sorted(set(refused)))}."
        )
    return text


def _vendors(file_metas: List[Dict]) -> str:
    """The vendor breakdown, sorted, because discovery order is rglob order."""
    counts: Dict[str, int] = {}
    for header in _headers(file_metas):
        name = header.vendor or UNSIGNED
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{name} ({count})" for name, count in sorted(counts.items()))


def _dimensions(headers: List[wsi.SlideHeader]) -> str:
    widths = [header.width for header in headers if header.width is not None]
    heights = [header.height for header in headers if header.height is not None]
    if not widths or not heights:
        return "unknown dimensions"
    return f"{_observed(widths)}x{_observed(heights)}"


def _observed(values: list) -> str:
    """What the batch holds: one value, a range of numbers, or a set of words.

    No ``Field.value`` is emitted anywhere, so this is where the numbers live.
    A field describes the whole batch, and one file's value would be a false
    statement about the others.
    """
    if all(isinstance(value, (int, float)) for value in values):
        low, high = min(values), max(values)
        return _number(low) if low == high else f"{_number(low)}-{_number(high)}"
    return ", ".join(sorted({str(value) for value in values}))


def _number(value) -> str:
    """A measurement, without the decimal point a whole one does not need.

    Not ``:g``: that switches to exponent notation above a million, and a
    slide 200,000 pixels wide is a routine size in this format.
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
