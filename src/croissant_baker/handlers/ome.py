"""Read the OME-XML document an OME-TIFF carries in its ImageDescription tag.

Pure: builds no Croissant, opens no file, and never touches pixel data. See
``docs/user-guide/supported-formats.md`` for what is done with the result.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: The TIFF tag OME-XML travels in: ImageDescription.
IMAGE_DESCRIPTION = 270

#: OME-XML larger than this is not parsed; a screening plate's reaches it. The
#: cap bounds the tree ElementTree would build and not the read, because
#: tifffile decodes tag 270 while opening the file, before any of this runs.
MAX_DESCRIPTION_BYTES = 8 << 20

#: Refusals. A file that earns one is still described — as a plain TIFF.
DECLARATION = "it declares a DTD or an entity"
OVERSIZED = "it is larger than {mib} MiB"
MALFORMED = "it is not well-formed"

# XML spells both in upper case, so a substring test finds them. Expat already
# caps entity amplification, so this refuses the bounded remainder — 353 bytes
# buying a megabyte — without depending on which Expat is linked. OME-XML
# carries no DTD, so nothing legitimate is refused.
_DECLARATIONS = ("<!DOCTYPE", "<!ENTITY")


@dataclass(frozen=True)
class OMEHeader:
    """The OME-XML attributes of one file, or the reason they were not read.

    Every ``Pixels`` attribute describes ``Image[0]``. One OME-XML document may
    declare several images — a multi-position acquisition does — and this
    describes one file, so ``image_count`` says how many it declared.

    Attributes:
        version: The schema version, from the root element's namespace.
        image_count: ``<Image>`` elements the document declares.
        binary_only: The document is a place-holder, and the schema forbids it
            any other metadata, so it describes nothing about the image.
        companion: The file a place-holder keeps its metadata in.
        refusal: Why the document was not parsed, empty if it was.
    """

    version: str = ""
    image_count: int = 0
    size_c: Optional[int] = None
    size_z: Optional[int] = None
    size_t: Optional[int] = None
    dimension_order: Optional[str] = None
    pixel_type: Optional[str] = None
    physical_size_x: Optional[float] = None
    physical_size_y: Optional[float] = None
    physical_size_unit: Optional[str] = None
    channel_names: Tuple[str, ...] = ()
    binary_only: bool = False
    companion: str = ""
    refusal: str = ""


def read(tif) -> Optional[OMEHeader]:
    """The OME header of an open TIFF, or None if it carries none.

    Args:
        tif: An open :class:`tifffile.TiffFile`.
    """
    # OME-ness is decided by content, and tifffile makes that decision. A
    # description it does not recognise as OME-XML — a truncated one, say — is
    # a plain TIFF rather than a refusal, because nothing identifies it as OME.
    if not tif.is_ome:
        return None
    page = tif.pages.first
    tag = page.tags.get(IMAGE_DESCRIPTION)
    if tag is None:
        return None
    if tag.valuebytecount > MAX_DESCRIPTION_BYTES:
        return OMEHeader(refusal=OVERSIZED.format(mib=MAX_DESCRIPTION_BYTES >> 20))
    return parse(page.description)


def parse(document: str) -> Optional[OMEHeader]:
    """Read an OME-XML document, or None if its root element is not ``OME``."""
    if any(token in document for token in _DECLARATIONS):
        return OMEHeader(refusal=DECLARATION)

    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        logger.debug(
            "an ImageDescription claiming to be OME-XML did not parse: %s", exc
        )
        return OMEHeader(refusal=MALFORMED)

    namespace, name = _split(root.tag)
    if name != "OME":
        return None

    def qualified(local: str) -> str:
        return f"{{{namespace}}}{local}" if namespace else local

    images = root.findall(qualified("Image"))
    pixels = images[0].find(qualified("Pixels")) if images else None
    attributes = pixels.attrib if pixels is not None else {}
    sidecar = root.find(qualified("BinaryOnly"))

    channels = ()
    if pixels is not None:
        named = [c.get("Name") for c in pixels.findall(qualified("Channel"))]
        # ``Name`` is optional, and a gap would misalign the rest of the list.
        channels = tuple(name for name in named if name)

    return OMEHeader(
        # The namespace is versioned — .../OME/2016-06 — so the version is read
        # off the document rather than matched against a constant.
        version=namespace.rsplit("/", 1)[-1] if namespace else "",
        image_count=len(images),
        size_c=_int(attributes, "SizeC"),
        size_z=_int(attributes, "SizeZ"),
        size_t=_int(attributes, "SizeT"),
        dimension_order=attributes.get("DimensionOrder"),
        pixel_type=attributes.get("Type"),
        physical_size_x=_float(attributes, "PhysicalSizeX"),
        physical_size_y=_float(attributes, "PhysicalSizeY"),
        physical_size_unit=attributes.get("PhysicalSizeXUnit"),
        channel_names=channels,
        binary_only=sidecar is not None,
        # ``MetadataFile``, not ``FileName``: that is TiffData/UUID's attribute,
        # for the multi-file case. Both name a file, and only one of them here.
        companion=sidecar.get("MetadataFile", "") if sidecar is not None else "",
    )


def _split(tag: str) -> Tuple[str, str]:
    """An ElementTree tag as ``(namespace, local name)``."""
    if tag.startswith("{"):
        namespace, _, name = tag[1:].partition("}")
        return namespace, name
    return "", tag


def _int(attributes: Dict[str, str], name: str) -> Optional[int]:
    """One malformed attribute costs that attribute, not the whole header."""
    try:
        return int(attributes[name])
    except (KeyError, ValueError):
        return None


def _float(attributes: Dict[str, str], name: str) -> Optional[float]:
    try:
        return float(attributes[name])
    except (KeyError, ValueError):
        return None
