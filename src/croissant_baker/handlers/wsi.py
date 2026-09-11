"""Read what a vendor whole-slide TIFF states about the slide it holds.

Pure: builds no Croissant, opens no file, and never decodes a pixel. Five
scanner vendors write their microscopy metadata into a TIFF container, each in
a private shape, and this module reads the shapes tifffile already identifies.
"""

from __future__ import annotations

import logging
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: Vendor XML larger than this is not parsed. The cap bounds the tree
#: ElementTree would build and not the read, because tifffile decodes the
#: description while opening the file, before any of this runs.
MAX_DESCRIPTION_BYTES = 8 << 20

#: Refusals, and the policy behind them, are :mod:`croissant_baker.handlers.ome`'s:
#: a file that earns one is still described, from its TIFF tags alone.
DECLARATION = "it declares a DTD or an entity"
OVERSIZED = "it is larger than {mib} MiB"
MALFORMED = "it is not well-formed"

#: ResolutionUnit 3. A vendor that states a pixel size in the baseline
#: resolution tags states it in pixels per centimetre, never per inch.
CENTIMETRE = 3

#: Micrometres in a centimetre.
MICRONS_PER_CENTIMETRE = 10000.0

#: Hamamatsu's private tag for the objective the scan was taken through.
SOURCE_LENS = 65421

#: The tag a Ventana scanner puts its XMP packet in.
XMP = 700

#: The vendors this module names. The order is tifffile's own series dispatch
#: order, so a file that trips two properties is attributed the same way here
#: as it is there.
APERIO = "aperio"
LEICA = "leica"
AKOYA = "akoya"
HAMAMATSU = "hamamatsu"
VENTANA = "ventana"

#: Vendor -> the ``TiffPage`` property tifffile recognises it by. Reusing
#: those properties rather than reimplementing them keeps one definition of
#: what an Aperio file is, in the library that tracks the vendors' output.
_VENDOR_FLAGS = (
    (APERIO, "is_svs"),
    (LEICA, "is_scn"),
    (AKOYA, "is_qpi"),
    (HAMAMATSU, "is_ndpi"),
    (VENTANA, "is_bif"),
)

#: The series kinds tifffile builds out of vendor knowledge, one per vendor
#: this module names. A series of one of these kinds holds the pyramid and
#: nothing else, whatever the pages around it look like.
VENDOR_SERIES_KINDS = frozenset({"svs", "scn", "qpi", "ndpi", "bif"})

#: The pictures of the slide, rather than of the tissue, that a scanner files
#: alongside the pyramid. tifffile names the series it recognises after these,
#: and anything it could not place is left unnamed rather than guessed at.
ASSOCIATED_KINDS = ("label", "macro", "overview", "thumbnail")


@dataclass(frozen=True)
class SlideHeader:
    """What one whole-slide file states about itself.

    Attributes:
        vendor: The scanner vendor that wrote the file, None when no vendor
            signature is present and the file is a plain TIFF.
        width: Base level width in pixels.
        height: Base level height in pixels.
        level_count: Pyramid levels, the base level included. One for a slide
            stored at a single resolution.
        level_dimensions: ``(width, height)`` per level, largest first.
        tile_width: Tile width of the base level, None for a stripped image.
        tile_height: Tile height of the base level, None for a stripped image.
        compression: The base level's compression, lowercased.
        associated_images: The kinds of associated image the file carries,
            drawn from :data:`ASSOCIATED_KINDS`, empty when it carries none.
        mpp_x: Micrometres per pixel across the base level.
        mpp_y: Micrometres per pixel down the base level.
        objective_power: The magnification of the objective the slide was
            scanned through.
        refusal: Why the vendor's own document was not parsed, empty if it
            was or if the file carries none.

    Leica states no ``mpp_x`` or ``mpp_y``: an SCN document puts the imaged
    area in its ``view`` element and the level sizes in ``pixels``, and
    dividing one by the other is an inference the file does not make.
    """

    vendor: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    level_count: int = 0
    level_dimensions: Tuple[Tuple[int, int], ...] = ()
    tile_width: Optional[int] = None
    tile_height: Optional[int] = None
    compression: Optional[str] = None
    associated_images: Tuple[str, ...] = ()
    mpp_x: Optional[float] = None
    mpp_y: Optional[float] = None
    objective_power: Optional[float] = None
    refusal: str = ""


def read(tif) -> SlideHeader:
    """The slide header of an open TIFF.

    Args:
        tif: An open :class:`tifffile.TiffFile`.
    """
    page = tif.pages.first
    vendor = _vendor(page)
    optics, refusal = _optics(vendor, page)
    # A document this module refused is one tifffile would parse too, on its
    # way to building a series, so a refused file is not asked for one and the
    # pyramid comes off the pages instead.
    series = () if refusal else _series(tif)
    levels = _levels(tif, series)
    return SlideHeader(
        vendor=vendor,
        mpp_x=optics.get("mpp_x"),
        mpp_y=optics.get("mpp_y"),
        objective_power=optics.get("objective_power"),
        refusal=refusal,
        width=levels[0][0] if levels else None,
        height=levels[0][1] if levels else None,
        level_count=len(levels),
        level_dimensions=levels,
        # Zero is how a stripped page reports a tile size it does not have.
        tile_width=getattr(page, "tilewidth", 0) or None,
        tile_height=getattr(page, "tilelength", 0) or None,
        compression=_compression(page),
        associated_images=_associated_images(series),
    )


def _vendor(page) -> Optional[str]:
    """The vendor that signed ``page``, or None for an unsigned TIFF."""
    for vendor, flag in _VENDOR_FLAGS:
        if getattr(page, flag, False):
            return vendor
    return None


def _compression(page) -> Optional[str]:
    """The base page's compression, by the name the TIFF specification gives it."""
    compression = getattr(page, "compression", None)
    if compression is None:
        return None
    return str(getattr(compression, "name", compression)).lower()


# What each vendor states about the optics, in the place that vendor states it


def _optics(vendor: Optional[str], page) -> Tuple[Dict[str, float], str]:
    """``(fields, refusal)`` for the vendor that wrote ``page``.

    An unsigned TIFF states nothing about a microscope, so it yields neither
    fields nor a refusal: nothing about it was declined.
    """
    reader = _OPTICS_READERS.get(vendor)
    if reader is None:
        return {}, ""
    return reader(page)


def _aperio_optics(page) -> Tuple[Dict[str, float], str]:
    """Aperio writes ``key = value`` items separated by pipes into tag 270.

    No refusal: the format is unspecified rather than malformed, so an item
    this does not recognise costs that item and nothing else. One ``MPP``
    covers both axes.
    """
    items = _pipe_separated(page.description)
    microns = _positive(items.get("MPP"))
    return {
        "mpp_x": microns,
        "mpp_y": microns,
        "objective_power": _positive(items.get("AppMag")),
    }, ""


def _pipe_separated(description: str) -> Dict[str, str]:
    """The ``key = value`` items of an Aperio description, by key.

    The first item is a free-text header rather than a pair, and an item
    without a ``=`` is skipped rather than failing the rest.
    """
    items = {}
    for item in description.split("|")[1:]:
        key, separator, value = item.partition("=")
        if separator:
            items[key.strip()] = value.strip()
    return items


def _hamamatsu_optics(page) -> Tuple[Dict[str, float], str]:
    """NDPI states the pixel size in the baseline resolution tags, and the
    objective in its own tag 65421."""
    mpp_x, mpp_y = _mpp_from_resolution(page)
    lens = page.tags.get(SOURCE_LENS)
    return {
        "mpp_x": mpp_x,
        "mpp_y": mpp_y,
        "objective_power": _positive(None if lens is None else lens.value),
    }, ""


def _leica_optics(page) -> Tuple[Dict[str, float], str]:
    """SCN states the objective, and no pixel size this module will infer."""
    root, refusal = _parse(page.description)
    if root is None:
        return {}, refusal
    objective = _first_text(root, "objective")
    return {"objective_power": _positive(objective)}, ""


def _ventana_optics(page) -> Tuple[Dict[str, float], str]:
    """Ventana states both on the ``iScan`` element of its XMP packet."""
    packet = page.tags.get(XMP)
    if packet is None:
        return {}, ""
    root, refusal = _parse(_as_text(packet.value))
    if root is None:
        return {}, refusal
    scan = _first_element(root, "iScan")
    if scan is None:
        return {}, ""
    microns = _positive(scan.get("ScanRes"))
    return {
        "mpp_x": microns,
        "mpp_y": microns,
        "objective_power": _positive(scan.get("Magnification")),
    }, ""


def _akoya_optics(page) -> Tuple[Dict[str, float], str]:
    """QPI states the objective in its XML and the pixel size in the tags.

    The objective is written the way it is printed on the lens, ``20x``, so
    the multiplication sign comes off before the number is read.
    """
    mpp_x, mpp_y = _mpp_from_resolution(page)
    fields: Dict[str, float] = {"mpp_x": mpp_x, "mpp_y": mpp_y}
    root, refusal = _parse(page.description)
    if root is None:
        return fields, refusal
    objective = _first_text(root, "Objective")
    if objective is not None:
        fields["objective_power"] = _positive(objective.rstrip("xX"))
    return fields, ""


#: Vendor -> the function that reads that vendor's optics.
_OPTICS_READERS = {
    APERIO: _aperio_optics,
    HAMAMATSU: _hamamatsu_optics,
    LEICA: _leica_optics,
    VENTANA: _ventana_optics,
    AKOYA: _akoya_optics,
}


def _mpp_from_resolution(page) -> Tuple[Optional[float], Optional[float]]:
    """The pixel size the baseline resolution tags state, per axis.

    Only centimetres. A file whose ResolutionUnit is inches or none at all is
    stating a print size or nothing, not a microscope's sampling interval.
    """
    unit = page.tags.get("ResolutionUnit")
    if unit is None or int(unit.value) != CENTIMETRE:
        return None, None
    return (
        _microns(page.tags.get("XResolution")),
        _microns(page.tags.get("YResolution")),
    )


def _microns(tag) -> Optional[float]:
    """One resolution tag, in pixels per centimetre, as microns per pixel."""
    if tag is None:
        return None
    value = tag.value
    numerator, denominator = value if isinstance(value, tuple) else (value, 1)
    if not numerator or not denominator:
        return None
    return MICRONS_PER_CENTIMETRE * denominator / numerator


def _positive(value) -> Optional[float]:
    """A measurement is only meaningful finite and above zero."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


# Reading a vendor's XML, under the refusal policy ome.py sets


class _DeclarationForbidden(ValueError):
    """A real declaration, as distinct from declaration text in an annotation."""


class _GuardedBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        # The parser calls this before processing the DTD's entity
        # declarations. Comments, processing instructions and CDATA never
        # trigger it.
        raise _DeclarationForbidden


def _parse(document: str) -> Tuple[Optional[ET.Element], str]:
    """``(root, refusal)``. Exactly one of the two is meaningful."""
    if not document:
        return None, ""
    if len(document.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
        return None, OVERSIZED.format(mib=MAX_DESCRIPTION_BYTES >> 20)
    try:
        root = ET.fromstring(document, parser=ET.XMLParser(target=_GuardedBuilder()))
    except _DeclarationForbidden:
        return None, DECLARATION
    except ET.ParseError as exc:
        logger.debug("a vendor slide description did not parse: %s", exc)
        return None, MALFORMED
    return root, ""


def _as_text(value) -> str:
    """A tag whose value tifffile hands back as bytes, as text."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _local(tag: str) -> str:
    """An ElementTree tag without its namespace. Vendors version theirs."""
    return tag.rpartition("}")[2]


def _first_element(root: ET.Element, name: str) -> Optional[ET.Element]:
    """The first element with this local name, root included."""
    for element in root.iter():
        if _local(element.tag) == name:
            return element
    return None


def _first_text(root: ET.Element, name: str) -> Optional[str]:
    element = _first_element(root, name)
    if element is None or element.text is None:
        return None
    return element.text.strip()


def _series(tif) -> tuple:
    """tifffile's own reading of the file's structure, or nothing.

    Building a series parses the vendor's XML, so a malformed document raises
    here rather than returning nothing. The file is still described from its
    tags in that case.
    """
    try:
        return tuple(tif.series)
    except Exception as exc:
        logger.debug("tifffile could not build a series for this slide: %s", exc)
        return ()


def _associated_images(series: tuple) -> Tuple[str, ...]:
    """The associated image kinds tifffile named, in a stable order.

    Only the vendor series builders name a series, so a file whose signature
    tifffile did not recognise reports none rather than a guess.
    """
    named = {s.name.lower() for s in series[1:] if s.name.lower() in ASSOCIATED_KINDS}
    return tuple(kind for kind in ASSOCIATED_KINDS if kind in named)


def _levels(tif, series: tuple) -> Tuple[Tuple[int, int], ...]:
    """The pyramid, largest level first.

    A series tifffile built with vendor knowledge is the whole answer: it
    already leaves out the label, macro and thumbnail pages, and some scanners
    store those in tiles, where a page walk cannot tell one from a level.

    The walk is for a file whose vendor signature is missing or unreadable.
    tifffile builds a generic series for one of those, and that series reports
    one level for a slide that has several, so the walk wins when it finds more.
    """
    if series and getattr(series[0], "kind", "") in VENDOR_SERIES_KINDS:
        return _levels_from_series(series)
    by_page = _levels_from_pages(tif)
    by_series = _levels_from_series(series)
    return by_series if len(by_series) >= len(by_page) else by_page


def _levels_from_series(series: tuple) -> Tuple[Tuple[int, int], ...]:
    """The levels of the first series, through tifffile's vendor knowledge."""
    if not series:
        return ()
    return tuple(
        (int(level.keyframe.imagewidth), int(level.keyframe.imagelength))
        for level in series[0].levels
        # tifffile leaves the keyframe unset for a page it could not read, and
        # dereferencing it would cost the whole file its description.
        if getattr(level, "keyframe", None) is not None
    )


def _levels_from_pages(tif) -> Tuple[Tuple[int, int], ...]:
    """The levels a page walk finds: tiled, same layout, strictly smaller.

    Label, macro and thumbnail pages are stored in strips rather than tiles in
    every vendor's output, which is what keeps them out of this.
    """
    page = tif.pages.first
    if page is None:
        return ()
    dimensions = [(int(page.imagewidth), int(page.imagelength))]
    if not page.is_tiled:
        return tuple(dimensions)

    layout = (page.photometric, page.samplesperpixel)
    for index in range(1, len(tif.pages)):
        other = tif.pages[index]
        if not getattr(other, "is_tiled", False):
            continue
        # A TiffFrame carries no tags of its own, so it reports neither, and
        # a file of frames is uniform rather than pyramidal anyway.
        if (
            getattr(other, "photometric", None),
            getattr(other, "samplesperpixel", None),
        ) != layout:
            continue
        width, height = int(other.imagewidth), int(other.imagelength)
        if width >= dimensions[-1][0] or height >= dimensions[-1][1]:
            continue
        dimensions.append((width, height))
    return tuple(dimensions)
