"""What a vendor whole-slide TIFF says about itself, read from its header alone."""

from __future__ import annotations

import io

import pytest
import tifffile

from croissant_baker.handlers import wsi

from tests.helpers import tiff_bytes, wsi_bytes


def open_bytes(data: bytes) -> tifffile.TiffFile:
    """The open TIFF the handler would hand the reader."""
    return tifffile.TiffFile(io.BytesIO(data))


# The synthetic slides, checked against tifffile's own vendor properties


@pytest.mark.parametrize(
    ("vendor", "flag"),
    [
        ("aperio", "is_svs"),
        ("hamamatsu", "is_ndpi"),
        ("leica", "is_scn"),
        ("ventana", "is_bif"),
        ("akoya", "is_qpi"),
    ],
)
def test_each_synthetic_slide_trips_the_tifffile_property_it_is_recognised_by(
    vendor: str, flag: str
) -> None:
    """The reader asks tifffile which vendor wrote a file, so a fixture that
    does not trip the property proves nothing about the vendor it claims."""
    with open_bytes(wsi_bytes(vendor)) as tif:
        assert getattr(tif.pages.first, flag) is True


def read_bytes(data: bytes) -> wsi.SlideHeader:
    """The header the handler would get for a slide holding ``data``."""
    with open_bytes(data) as tif:
        return wsi.read(tif)


# Which vendor wrote the file


@pytest.mark.parametrize("vendor", ["aperio", "hamamatsu", "leica", "ventana", "akoya"])
def test_every_vendor_is_named_by_the_signal_it_writes(vendor: str) -> None:
    assert read_bytes(wsi_bytes(vendor)).vendor == vendor


def test_a_tiff_no_vendor_signed_has_no_vendor() -> None:
    """A plain TIFF renamed to a vendor extension is still described, because
    its tags say as much about it as any other TIFF's do."""
    assert read_bytes(tiff_bytes()).vendor is None


# The pyramid


def test_the_base_level_dimensions_come_from_the_largest_page() -> None:
    header = read_bytes(wsi_bytes("aperio"))

    assert (header.width, header.height) == (256, 256)


def test_the_levels_are_listed_largest_first() -> None:
    header = read_bytes(wsi_bytes("aperio"))

    assert header.level_dimensions == ((256, 256), (128, 128))


def test_the_label_macro_and_thumbnail_pages_are_not_pyramid_levels() -> None:
    """The Aperio fixture has five pages and two of them are levels. Counting
    pages instead would report a five-level pyramid for a two-level slide."""
    header = read_bytes(wsi_bytes("aperio"))

    assert header.level_count == 2


@pytest.mark.parametrize(
    ("vendor", "level_count"),
    [
        ("aperio", 2),
        ("hamamatsu", 1),
        ("leica", 2),
        ("ventana", 2),
        ("akoya", 2),
    ],
)
def test_every_vendor_pyramid_is_counted(vendor: str, level_count: int) -> None:
    assert read_bytes(wsi_bytes(vendor)).level_count == level_count


def test_a_single_page_tiff_is_a_one_level_pyramid() -> None:
    header = read_bytes(tiff_bytes())

    assert header.level_count == 1
    assert header.level_dimensions == ((8, 8),)


# How the base level is stored


def test_the_tile_size_comes_from_the_base_page() -> None:
    header = read_bytes(wsi_bytes("aperio"))

    assert (header.tile_width, header.tile_height) == (128, 128)


def test_a_stripped_image_states_no_tile_size() -> None:
    """None rather than the image width: a strip is not a tile, and a reader
    planning tile requests would issue one request per row."""
    header = read_bytes(tiff_bytes())

    assert (header.tile_width, header.tile_height) == (None, None)


@pytest.mark.parametrize(
    ("data", "compression"),
    [(wsi_bytes("aperio"), "deflate"), (tiff_bytes(), "none")],
    ids=["deflate", "uncompressed"],
)
def test_the_base_page_names_its_compression(data: bytes, compression: str) -> None:
    assert read_bytes(data).compression == compression


# Associated images


@pytest.mark.parametrize(
    ("vendor", "kinds"),
    [
        ("aperio", ("label", "macro", "thumbnail")),
        ("hamamatsu", ()),
        ("leica", ()),
        ("ventana", ("label",)),
        ("akoya", ("thumbnail",)),
    ],
)
def test_the_associated_images_a_vendor_stored_are_named(vendor, kinds) -> None:
    """The slide overview, the barcode label and the low-power thumbnail are
    pictures of the slide rather than of the tissue, and a consumer asking for
    a region wants to know they are in the file and are not levels."""
    assert read_bytes(wsi_bytes(vendor)).associated_images == kinds


def test_a_plain_tiff_carries_no_associated_images() -> None:
    assert read_bytes(tiff_bytes()).associated_images == ()


# The optics


@pytest.mark.parametrize(
    ("vendor", "mpp"),
    [("aperio", 0.4990), ("hamamatsu", 0.46), ("ventana", 0.25), ("akoya", 0.5)],
)
def test_the_microns_per_pixel_each_vendor_states_are_read(vendor, mpp) -> None:
    header = read_bytes(wsi_bytes(vendor))

    assert (header.mpp_x, header.mpp_y) == (mpp, mpp)


def test_a_leica_slide_states_no_microns_per_pixel() -> None:
    """SCN puts the imaged area in the ``view`` element and the level sizes in
    ``pixels``, and dividing one by the other is an inference the file does not
    make. Left unread rather than guessed at."""
    header = read_bytes(wsi_bytes("leica"))

    assert (header.mpp_x, header.mpp_y) == (None, None)


@pytest.mark.parametrize(
    ("vendor", "objective"),
    [
        ("aperio", 20.0),
        ("hamamatsu", 20.0),
        ("leica", 40.0),
        ("ventana", 40.0),
        ("akoya", 20.0),
    ],
)
def test_the_objective_power_each_vendor_states_is_read(vendor, objective) -> None:
    assert read_bytes(wsi_bytes(vendor)).objective_power == objective


def test_a_slide_stating_no_optics_leaves_them_unread() -> None:
    """Both items are optional in the Aperio description, and a default would
    be a magnification nobody measured."""
    header = read_bytes(wsi_bytes("aperio", description="Aperio Image Library\r\n256"))

    assert (header.mpp_x, header.mpp_y, header.objective_power) == (None, None, None)


def test_a_plain_tiff_states_no_optics() -> None:
    header = read_bytes(tiff_bytes())

    assert (header.mpp_x, header.mpp_y, header.objective_power) == (None, None, None)


# Refusals


SCN_ROOT = '<scn xmlns="http://www.leica-microsystems.com/scn/2010/10/01">'

#: One entity declaration is enough: the refusal is on the declaration itself,
#: not on how far the expansion would have got.
SCN_BOMB = (
    '<?xml version="1.0"?>\n<!DOCTYPE scn [\n<!ENTITY a "lol">\n]>\n'
    f'{SCN_ROOT}<collection name="&a;"/></scn>'
)

SCN_MALFORMED = f"{SCN_ROOT}<collection></scn>"


@pytest.mark.parametrize(
    ("xml", "refusal"),
    [(SCN_BOMB, wsi.DECLARATION), (SCN_MALFORMED, wsi.MALFORMED)],
    ids=["entity declaration", "not well-formed"],
)
def test_a_vendor_document_that_cannot_be_parsed_is_refused(xml, refusal) -> None:
    assert read_bytes(wsi_bytes("leica", xml=xml)).refusal == refusal


def test_an_oversized_vendor_document_is_refused(monkeypatch) -> None:
    """The cap bounds the tree ElementTree would build, not the read: tifffile
    has already decoded tag 270 by the time any of this runs."""
    monkeypatch.setattr(wsi, "MAX_DESCRIPTION_BYTES", 8)

    assert read_bytes(wsi_bytes("leica")).refusal == wsi.OVERSIZED.format(mib=0)


def test_a_refused_slide_is_still_described_from_its_tiff_tags() -> None:
    """The refusal costs the vendor's own metadata and nothing else. The
    pyramid comes from the pages, because tifffile builds a series by parsing
    the same document this module just refused."""
    header = read_bytes(wsi_bytes("leica", xml=SCN_MALFORMED))

    assert (header.width, header.height, header.level_count) == (256, 256, 2)


@pytest.mark.parametrize("vendor", ["aperio", "hamamatsu", "leica", "ventana", "akoya"])
def test_a_slide_whose_document_parsed_carries_no_refusal(vendor: str) -> None:
    assert read_bytes(wsi_bytes(vendor)).refusal == ""
