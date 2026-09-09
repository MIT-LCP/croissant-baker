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
    "physical_size_x_unit": "µm",
    "physical_size_y_unit": "mm",
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
                "physical_size_x_unit": None,
                "physical_size_y_unit": None,
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
                "physical_size_x_unit": None,
                "physical_size_y_unit": None,
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


@pytest.mark.parametrize("version", ["2016-06", "2013-06"])
@pytest.mark.parametrize("axis", ["X", "Y"])
def test_an_omitted_unit_defaults_only_the_axis_with_a_measurement(version, axis):
    header = ome.parse(
        ome_xml(
            image(pixels=f'PhysicalSize{axis}="0.65"'),
            namespace=f"http://www.openmicroscopy.org/Schemas/OME/{version}",
        )
    )
    assert getattr(header, f"physical_size_{axis.lower()}") == 0.65
    assert getattr(header, f"physical_size_{axis.lower()}_unit") == "µm"
    other = "y" if axis == "X" else "x"
    assert getattr(header, f"physical_size_{other}") is None
    assert getattr(header, f"physical_size_{other}_unit") is None


@pytest.mark.parametrize("unit", ["nm", "mm", "µm", "reference frame", ""])
def test_explicit_units_are_preserved_without_conversion(unit):
    header = ome.parse(
        ome_xml(
            image(
                pixels=f'PhysicalSizeX="0.65" PhysicalSizeXUnit="{unit}" PhysicalSizeY="2"'
            )
        )
    )
    assert (header.physical_size_x, header.physical_size_x_unit) == (0.65, unit)
    assert (header.physical_size_y, header.physical_size_y_unit) == (2, "µm")


@pytest.mark.parametrize("value", ["NaN", "inf", "-inf", "1e309", "0", "-1", "wide"])
def test_invalid_spacing_does_not_corrupt_the_other_axis(value):
    header = ome.parse(
        ome_xml(
            image(
                pixels=f'PhysicalSizeX="{value}" PhysicalSizeXUnit="mm" PhysicalSizeY="2"'
            )
        )
    )
    assert header.physical_size_x is None
    assert header.physical_size_x_unit is None
    assert (header.physical_size_y, header.physical_size_y_unit) == (2, "µm")


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "lots"])
def test_invalid_dimension_counts_are_omitted_independently(value):
    header = ome.parse(ome_xml(image(pixels=f'SizeC="{value}" SizeZ="2" SizeT="3"')))
    assert (header.size_c, header.size_z, header.size_t) == (None, 2, 3)


@pytest.mark.parametrize(
    "document",
    [
        "<MetaData><plane/></MetaData>",
        '<OME xmlns="urn:unrelated"><Image><Pixels PhysicalSizeX="1"/></Image></OME>',
        '<OME><Image><Pixels PhysicalSizeX="1"/></Image></OME>',
        '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06-extra"/>',
    ],
)
def test_a_root_that_is_not_ome_is_not_an_ome_header(document) -> None:
    """Well-formed XML in tag 270 is common — ImageJ, MetaSeries, Leica SCN.
    Only reachable directly: ``read`` stops at ``tif.is_ome`` first."""
    assert ome.parse(document) is None


def test_channel_names_keep_document_order_and_skip_the_unnamed() -> None:
    """The list contains declared labels, not a positional channel mapping."""
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
        ome_xml(image()).replace("<OME", "<!-- <!DOCTYPE example> --><OME", 1),
        ome_xml("<!-- <!ENTITY example 'text'> -->" + image()),
        ome_xml(image()) + "<!-- <!DOCTYPE example> -->",
        ome_xml(image()).replace("<OME", "<?annotation <!DOCTYPE example> ?><OME", 1),
        ome_xml(
            image()
            + '<StructuredAnnotations><XMLAnnotation ID="Annotation:0"><Value>'
            + "<![CDATA[<!DOCTYPE example [<!ENTITY label 'text'>]><example/>]]>"
            + "</Value></XMLAnnotation></StructuredAnnotations>"
        ),
    ],
    ids=["prolog comment", "body comment", "trailing comment", "PI", "CDATA"],
)
def test_declaration_text_in_annotations_does_not_discard_the_header(document):
    assert ome.parse(document) == ome.parse(ome_xml(image()))


@pytest.mark.parametrize(
    "declaration",
    [
        "<!DOCTYPE OME>",
        '<!DOCTYPE OME SYSTEM "file:///nonexistent.dtd">',
        '<!DOCTYPE OME PUBLIC "example" "https://example.invalid/ome.dtd">',
        '<!DOCTYPE OME [<!ENTITY example "text">]>',
    ],
    ids=["bare", "external system", "external public", "internal entity"],
)
def test_real_declarations_are_refused_even_after_a_comment(declaration):
    document = ome_xml(image()).replace(
        "<OME", "<!-- <OME/> -->" + declaration + "<OME", 1
    )
    assert ome.parse(document).refusal == ome.DECLARATION


def test_entity_expansion_is_stopped_by_the_declaration_guard():
    assert ome.parse(bomb(8)).refusal == ome.DECLARATION


@pytest.mark.parametrize("token", ["<!DOCTYPE OME>", "<!ENTITY example 'text'>"])
def test_declarations_after_the_root_are_malformed(token):
    assert ome.parse(ome_xml(image()) + token).refusal == ome.MALFORMED


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


def test_direct_parsing_also_bounds_utf8_bytes_before_building_a_tree(monkeypatch):
    document = ome_xml(image(channels=("µm" * 100,)))
    monkeypatch.setattr(ome, "MAX_DESCRIPTION_BYTES", len(document))

    def fail(_document):
        raise AssertionError("oversized XML reached the parser")

    monkeypatch.setattr(ome.ET, "fromstring", fail)
    assert ome.parse(document).refusal


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
