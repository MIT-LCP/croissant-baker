"""What an OME-TIFF's header says about itself, read from the XML alone."""

from __future__ import annotations

import io

import pytest
import tifffile

from croissant_baker.handlers import ome

from tests.helpers import (
    OME_NAMESPACE,
    OME_PIXELS,
    ome_bomb as bomb,
    ome_image as image,
    ome_xml,
    tiff_bytes,
)


def read_bytes(data: bytes):
    """The header the handler would get for a TIFF holding ``data``."""
    with tifffile.TiffFile(io.BytesIO(data)) as tif:
        return ome.read(tif)


# --------------------------------------------------------------------------
# What the parser reads
# --------------------------------------------------------------------------


READ = {
    "size_c": 3,
    "size_z": 1,
    "size_t": 5,
    "dimension_order": "XYCZT",
    "pixel_type": "uint16",
    "physical_size_x": 0.2125,
    "physical_size_y": 0.425,
    "physical_size_unit": "µm",
    "channel_names": ("DAPI", "ATP1A1", "18S"),
    "image_count": 1,
    "refusal": "",
}


@pytest.mark.parametrize(
    ("pixels", "expected"),
    [
        (OME_PIXELS, READ),
        # PhysicalSizeX is optional in the schema, and a default would be a
        # made-up measurement.
        (
            'DimensionOrder="XYCZT" Type="uint8" SizeC="1"',
            {
                **READ,
                "size_c": 1,
                "size_z": None,
                "size_t": None,
                "pixel_type": "uint8",
                "physical_size_x": None,
                "physical_size_y": None,
                "physical_size_unit": None,
            },
        ),
        # One malformed attribute costs that attribute, not the whole header.
        (
            'SizeC="lots" SizeZ="2" PhysicalSizeX="wide"',
            {
                **READ,
                "size_c": None,
                "size_z": 2,
                "size_t": None,
                "dimension_order": None,
                "pixel_type": None,
                "physical_size_x": None,
                "physical_size_y": None,
                "physical_size_unit": None,
            },
        ),
    ],
    ids=["every attribute", "absent", "unreadable"],
)
def test_what_the_pixels_element_says_reaches_the_header(
    pixels: str, expected: dict
) -> None:
    header = ome.parse(ome_xml(image(pixels=pixels)))

    assert header is not None
    assert {name: getattr(header, name) for name in expected} == expected


@pytest.mark.parametrize("version", ["2016-06", "2013-06"])
def test_the_schema_version_comes_from_the_root_element(version: str) -> None:
    """The namespace is versioned, so matching a constant would read one year
    of files and silently decline the rest."""
    namespace = f"http://www.openmicroscopy.org/Schemas/OME/{version}"

    header = ome.parse(ome_xml(image(), namespace=namespace))

    assert header is not None
    assert header.version == version


def test_a_root_that_is_not_ome_is_not_an_ome_header() -> None:
    """Well-formed XML in tag 270 is common — ImageJ, MetaSeries, Leica SCN.
    Only reachable directly: ``read`` stops at ``tif.is_ome`` first."""
    assert ome.parse("<MetaData><plane/></MetaData>") is None


def test_channel_names_keep_document_order_and_skip_the_unnamed() -> None:
    """``Name`` is optional, and a gap in the list would misalign the rest."""
    header = ome.parse(
        ome_xml(
            '<Image ID="Image:0"><Pixels ID="Pixels:0" SizeC="3">'
            '<Channel ID="Channel:0:0" Name="DAPI"/>'
            '<Channel ID="Channel:0:1"/>'
            '<Channel ID="Channel:0:2" Name="18S"/>'
            "</Pixels></Image>"
        )
    )

    assert header is not None
    assert header.channel_names == ("DAPI", "18S")


def test_the_pixels_fields_describe_the_first_image() -> None:
    """A multi-position acquisition declares several images in one file. One
    row per file means the row can only describe one of them, so it says which."""
    header = ome.parse(
        ome_xml(
            image()
            + image(
                identifier="Image:1",
                pixels='SizeC="40" Type="uint8"',
                channels=("CD3",),
            )
        )
    )

    assert header is not None
    assert header.image_count == 2
    assert header.size_c == 3
    assert header.pixel_type == "uint16"
    assert header.channel_names == ("DAPI", "ATP1A1", "18S")


# --------------------------------------------------------------------------
# What the parser refuses
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        bomb(6),
        f'<!DOCTYPE OME><OME xmlns="{OME_NAMESPACE}"/>',
        f'<OME xmlns="{OME_NAMESPACE}"><Image',
    ],
    ids=["entity bomb", "bare doctype", "not well-formed"],
)
def test_a_document_that_cannot_be_trusted_is_refused_not_raised(
    document: str,
) -> None:
    """OME-XML carries no DTD — its root is ``<OME xmlns=…>`` behind at most an
    XML declaration — so refusing one loses nothing legitimate, and it does not
    depend on which Expat the user happens to have linked."""
    header = ome.parse(document)

    assert header is not None
    assert header.refusal
    assert header.size_c is None
    assert header.channel_names == ()


def test_the_entity_bomb_would_otherwise_have_expanded() -> None:
    """Guards the fixture, not the parser: were Expat to start refusing a
    six-level bomb, the test above would pass without the check it exists for."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(bomb(6))

    assert len(root[0].get("ID")) > 100_000


def test_an_oversized_description_is_not_parsed(monkeypatch) -> None:
    """A high-content-screening plate's OME-XML reaches this size, and
    describing 384 wells is not what this handler is for."""

    def fail(_document: str):
        raise AssertionError("the document was parsed despite exceeding the cap")

    monkeypatch.setattr(ome.ET, "fromstring", fail)

    header = read_bytes(
        tiff_bytes(ome_xml(f"<!--{'x' * (ome.MAX_DESCRIPTION_BYTES + 1)}-->"))
    )

    assert header is not None
    assert header.refusal
    assert header.size_c is None


# --------------------------------------------------------------------------
# What a TIFF has to carry before any of it applies
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "reads_ome"),
    [
        (None, False),
        ("ImageJ=1.53t\nimages=1\nslices=1\n", False),
        ("<MetaData><PlaneInfo/></MetaData>", False),
        (ome_xml(image()), True),
    ],
    ids=["no description at all", "ImageJ", "XML that is not OME", "OME-XML"],
)
def test_only_an_ome_tiff_yields_a_header(
    description: str | None, reads_ome: bool
) -> None:
    """None rather than a refusal for the rest: those files are described as
    plain TIFFs, and nothing about them was declined."""
    header = read_bytes(tiff_bytes(description, planes=3 if reads_ome else 1))

    if not reads_ome:
        assert header is None
        return
    assert header is not None
    assert (header.size_c, header.version) == (3, "2016-06")
