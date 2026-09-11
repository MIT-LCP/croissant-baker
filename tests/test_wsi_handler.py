"""What the whole-slide handler claims, reads and refuses."""

from __future__ import annotations

from pathlib import Path

import pytest

from croissant_baker.handlers.wsi_handler import WSIHandler
from croissant_baker.sources import make_source

from tests.helpers import (
    APERIO_SVS,
    SCN_MALFORMED,
    WRAPPER_SUFFIXES,
    wsi_bytes,
    write_wrapped,
)

#: The vendor whose scanner writes each extension. A slide has to be the
#: format its name claims: tifffile reads a little-endian classic TIFF named
#: ``.ndpi`` with the 64-bit offsets a real NDPI has, and finds no page in a
#: file that does not have them.
VENDOR_EXTENSIONS = {
    ".svs": "aperio",
    ".ndpi": "hamamatsu",
    ".scn": "leica",
    ".bif": "ventana",
    ".qptiff": "akoya",
}


@pytest.fixture
def handler() -> WSIHandler:
    return WSIHandler()


# Claiming


#: Classic TIFF and BigTIFF, in both byte orders. Every vendor here writes a
#: TIFF container, and three of the five reach BigTIFF on a routine slide.
TIFF_HEADERS = {
    "classic little-endian": b"II*\x00",
    "classic big-endian": b"MM\x00*",
    "bigtiff little-endian": b"II+\x00",
    "bigtiff big-endian": b"MM\x00+",
}


@pytest.mark.parametrize("extension", WSIHandler.EXTENSIONS)
@pytest.mark.parametrize("header", list(TIFF_HEADERS.values()), ids=list(TIFF_HEADERS))
def test_every_declared_extension_is_claimed_at_every_tiff_magic(
    handler: WSIHandler, dataset: Path, extension: str, header: bytes
) -> None:
    path = write_wrapped(dataset, f"slide{extension}", header)

    assert handler.claims(make_source(path)) is True


@pytest.mark.parametrize("extension", WSIHandler.EXTENSIONS)
def test_a_vendor_extension_over_bytes_that_are_not_a_tiff_is_not_claimed(
    handler: WSIHandler, dataset: Path, extension: str
) -> None:
    """The extension is a filename, and a rename is free. Claiming this file
    would promise an extraction that then fails the whole batch."""
    path = write_wrapped(dataset, f"slide{extension}", b"<!DOCTYPE html><html>")

    assert handler.claims(make_source(path)) is False


@pytest.mark.parametrize("extension", WSIHandler.EXTENSIONS)
def test_a_shouted_extension_is_the_same_extension(
    handler: WSIHandler, dataset: Path, extension: str
) -> None:
    path = write_wrapped(dataset, f"SLIDE{extension.upper()}", b"II*\x00")

    assert handler.claims(make_source(path)) is True


@pytest.mark.parametrize("suffix", WRAPPER_SUFFIXES)
def test_a_compressed_slide_is_claimed_through_its_wrapper(
    handler: WSIHandler, dataset: Path, suffix: str
) -> None:
    """A whole-slide file is large, so it arrives compressed more often than
    most. The handler is given the logical name and the decompressed bytes."""
    path = write_wrapped(dataset, "slide.svs", APERIO_SVS, suffix)

    assert handler.claims(make_source(path, Path("slide.svs"))) is True


def test_a_plain_tiff_is_left_to_the_image_handler(
    handler: WSIHandler, dataset: Path
) -> None:
    """``.tif`` is not a vendor extension, and nothing in the bytes of a
    pyramidal TIFF says the file holds a slide rather than a satellite scene."""
    path = write_wrapped(dataset, "scene.tif", APERIO_SVS)

    assert handler.claims(make_source(path)) is False


# Reading one file


def test_a_slide_is_described_by_the_keys_the_generator_needs(
    handler: WSIHandler, dataset: Path
) -> None:
    path = write_wrapped(dataset, "slide.svs", APERIO_SVS)

    meta = handler.extract(make_source(path, Path("slide.svs")))

    assert meta["file_name"] == "slide.svs"
    assert meta["file_size"] == len(APERIO_SVS)
    assert len(meta["sha256"]) == 64
    assert (meta["width"], meta["height"]) == (256, 256)


@pytest.mark.parametrize(
    ("extension", "vendor"), list(VENDOR_EXTENSIONS.items()), ids=VENDOR_EXTENSIONS
)
def test_every_vendor_extension_is_a_tiff_container(
    handler: WSIHandler, dataset: Path, extension: str, vendor: str
) -> None:
    """No vendor media type is registered, and a made-up one would be a
    statement no reader can act on."""
    path = write_wrapped(dataset, f"slide{extension}", wsi_bytes(vendor))

    meta = handler.extract(make_source(path, Path(f"slide{extension}")))

    assert meta["encoding_format"] == "image/tiff"


def test_the_slide_header_reaches_the_metadata(
    handler: WSIHandler, dataset: Path
) -> None:
    path = write_wrapped(dataset, "slide.svs", APERIO_SVS)

    meta = handler.extract(make_source(path, Path("slide.svs")))

    assert meta["slide"].vendor == "aperio"
    assert meta["slide"].level_count == 2


def test_bytes_that_are_not_a_tiff_raise_a_value_error_naming_the_file(
    handler: WSIHandler, dataset: Path
) -> None:
    """The message becomes the reason detail a user reads in ``--report``."""
    path = write_wrapped(dataset, "slide.svs", b"\x00\xff not a slide \xfe\x00")

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path, Path("nested/slide.svs")))

    assert "nested/slide.svs" in str(caught.value)


def test_a_reader_error_with_a_bare_number_for_a_message_names_its_type(
    handler: WSIHandler, dataset: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tifffile raises with a file offset for a message often enough that
    "Failed to read whole-slide image slide.svs: 0" is what a user is left
    with in ``--report``."""
    from croissant_baker.handlers import wsi

    def fail(tif) -> None:
        raise IndexError("0")

    monkeypatch.setattr(wsi, "read", fail)
    path = write_wrapped(dataset, "slide.svs", APERIO_SVS)

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path, Path("slide.svs")))

    assert "IndexError: 0" in str(caught.value)


def test_a_reader_error_that_says_something_is_quoted_as_it_stands(
    handler: WSIHandler, dataset: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A message a user can act on needs no type name in front of it."""
    from croissant_baker.handlers import wsi

    def fail(tif) -> None:
        raise ValueError("not a TIFF file")

    monkeypatch.setattr(wsi, "read", fail)
    path = write_wrapped(dataset, "slide.svs", APERIO_SVS)

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path, Path("slide.svs")))

    assert str(caught.value).endswith("slide.svs: not a TIFF file")


def test_a_missing_slide_raises_file_not_found(
    handler: WSIHandler, dataset: Path
) -> None:
    source = make_source(dataset / "gone" / "slide.svs", Path("slide.svs"))

    with pytest.raises(FileNotFoundError):
        handler.extract(source)


def test_a_refused_vendor_document_is_logged_against_the_file(
    handler: WSIHandler, dataset: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The scan report clears the reason once a file is described, so a
    partial refusal on a described file has nowhere else to be seen."""
    path = write_wrapped(dataset, "slide.scn", wsi_bytes("leica", xml=SCN_MALFORMED))

    with caplog.at_level("WARNING", logger="croissant_baker.handlers.wsi_handler"):
        meta = handler.extract(make_source(path, Path("slide.scn")))

    assert meta["slide"].refusal
    assert [r for r in caplog.records if "slide.scn" in r.message]


# Describing a batch


def batch(handler: WSIHandler, dataset: Path, *names: str) -> tuple:
    """Extract every named slide, in the shape ``build_croissant`` is given."""
    metas, ids = [], []
    for index, name in enumerate(names):
        extension = Path(name).suffix
        path = write_wrapped(dataset, name, wsi_bytes(VENDOR_EXTENSIONS[extension]))
        metas.append(handler.extract(make_source(path, Path(name))))
        ids.append(f"file_{index}")
    return metas, ids


def test_an_empty_batch_describes_nothing(handler: WSIHandler) -> None:
    """A FileSet over zero files would describe data that is not there."""
    result = handler.build_croissant([], [])

    assert (result.file_sets, result.record_sets) == ([], [])


def test_the_file_set_globs_only_the_extensions_the_batch_holds(
    handler: WSIHandler, dataset: Path
) -> None:
    """A ``.bif`` include beside no Ventana slide names a file the dataset
    does not have. Both glob forms, because mlcroissant's fnmatch reader
    requires a directory before ``**/`` and the files may sit at the root."""
    metas, ids = batch(handler, dataset, "a.svs", "b.scn")

    (file_set,) = handler.build_croissant(metas, ids).file_sets

    assert sorted(file_set.includes) == ["**/*.scn", "**/*.svs", "*.scn", "*.svs"]


def test_the_file_set_names_the_container_media_type(
    handler: WSIHandler, dataset: Path
) -> None:
    metas, ids = batch(handler, dataset, "a.svs")

    (file_set,) = handler.build_croissant(metas, ids).file_sets

    assert file_set.encoding_formats == ["image/tiff"]


def test_the_record_set_has_one_field_per_thing_the_batch_stated(
    handler: WSIHandler, dataset: Path
) -> None:
    metas, ids = batch(handler, dataset, "a.svs")

    (record_set,) = handler.build_croissant(metas, ids).record_sets

    assert [field.name for field in record_set.fields] == [
        "image",
        "filename",
        "vendor",
        "width",
        "height",
        "level_count",
        "tile_width",
        "tile_height",
        "mpp_x",
        "mpp_y",
        "objective_power",
    ]


def test_a_batch_of_stripped_slides_carries_no_tile_size(
    handler: WSIHandler, dataset: Path
) -> None:
    """A strip is not a tile, and a tile size on a stripped slide would send a
    reader planning tile requests one request per row."""
    metas, ids = batch(handler, dataset, "a.ndpi")

    (record_set,) = handler.build_croissant(metas, ids).record_sets

    names = [field.name for field in record_set.fields]
    assert "tile_width" not in names and "tile_height" not in names


def test_the_record_set_description_names_the_compressions_the_batch_uses(
    handler: WSIHandler, dataset: Path
) -> None:
    """There is no compression field: one slide's codec is a fact about how
    that file stores its tiles, so the batch states the set it holds."""
    metas, ids = batch(handler, dataset, "a.svs")

    (record_set,) = handler.build_croissant(metas, ids).record_sets

    assert "Compression: deflate." in record_set.description


def test_the_record_set_description_names_the_associated_image_kinds(
    handler: WSIHandler, dataset: Path
) -> None:
    """The barcode label and the low-power macro are in the file and are not
    levels, and a consumer asking for a region wants to know it."""
    metas, ids = batch(handler, dataset, "a.svs", "b.bif")

    (record_set,) = handler.build_croissant(metas, ids).record_sets

    assert "Associated images: label, macro, thumbnail." in record_set.description


def test_a_batch_carrying_no_associated_image_says_nothing_about_them(
    handler: WSIHandler, dataset: Path
) -> None:
    metas, ids = batch(handler, dataset, "a.scn")

    (record_set,) = handler.build_croissant(metas, ids).record_sets

    assert "Associated images" not in record_set.description


#: Each capability :attr:`WSIHandler.FORMAT_DESCRIPTION` promises, against the
#: field name or the description phrase that delivers it. The generated
#: formats table is read as a promise about the document, and the two drifted
#: once already: tile size and associated images were advertised and emitted
#: nowhere.
PROMISED = {
    "vendor": "vendor",
    "pyramid levels": "level_count",
    "tile size": "tile_width",
    "microns per pixel": "mpp_x",
    "objective magnification": "objective_power",
    "associated images": "Associated images:",
}


@pytest.mark.parametrize(("noun", "stated_as"), sorted(PROMISED.items()))
def test_every_capability_the_format_line_promises_reaches_the_document(
    handler: WSIHandler, dataset: Path, noun: str, stated_as: str
) -> None:
    assert noun in WSIHandler.FORMAT_DESCRIPTION.lower()

    metas, ids = batch(
        handler, dataset, *(f"{v}{e}" for e, v in VENDOR_EXTENSIONS.items())
    )

    (record_set,) = handler.build_croissant(metas, ids).record_sets

    names = {field.name for field in record_set.fields}
    assert stated_as in names or stated_as in record_set.description


def test_a_field_no_slide_in_the_batch_stated_is_not_emitted(
    handler: WSIHandler, dataset: Path
) -> None:
    """A Leica slide states no pixel size, so a batch of them must not carry
    an ``mpp_x`` field describing a measurement nothing in the batch made."""
    metas, ids = batch(handler, dataset, "a.scn")

    (record_set,) = handler.build_croissant(metas, ids).record_sets

    assert "mpp_x" not in [field.name for field in record_set.fields]


def test_every_field_says_what_the_batch_holds(
    handler: WSIHandler, dataset: Path
) -> None:
    """No ``Field.value`` is emitted anywhere, so the observed values live in
    the descriptions or nowhere."""
    metas, ids = batch(handler, dataset, "a.svs")

    (record_set,) = handler.build_croissant(metas, ids).record_sets
    fields = {field.name: field.description for field in record_set.fields}

    assert fields["vendor"].endswith("(aperio)")
    assert fields["objective_power"].endswith("(20)")
    assert fields["mpp_x"].endswith("(0.499)")


def test_the_record_set_description_breaks_the_batch_down_by_vendor(
    handler: WSIHandler, dataset: Path
) -> None:
    metas, ids = batch(handler, dataset, "a.svs", "b.svs", "c.scn")

    (record_set,) = handler.build_croissant(metas, ids).record_sets

    assert "aperio (2), leica (1)" in record_set.description


def test_the_record_set_description_states_the_dimension_range(
    handler: WSIHandler, dataset: Path
) -> None:
    metas, ids = batch(handler, dataset, "a.svs", "b.ndpi")

    (record_set,) = handler.build_croissant(metas, ids).record_sets

    assert "64-256x64-256" in record_set.description


def test_the_record_set_description_counts_the_slides_it_could_not_read(
    handler: WSIHandler, dataset: Path
) -> None:
    """A described file's partial refusal has nowhere else to be seen: the
    scan report clears the reason once the file is described."""
    metas, ids = batch(handler, dataset, "a.svs")
    path = write_wrapped(dataset, "b.scn", wsi_bytes("leica", xml=SCN_MALFORMED))
    broken = handler.extract(make_source(path, Path("b.scn")))

    (record_set,) = handler.build_croissant([*metas, broken], [*ids, "f"]).record_sets

    assert "1 of 2" in record_set.description
    assert "it is not well-formed" in record_set.description


# Through the pipeline


@pytest.mark.parametrize(
    ("extension", "vendor"), list(VENDOR_EXTENSIONS.items()), ids=VENDOR_EXTENSIONS
)
def test_the_registry_routes_every_vendor_extension_here(
    dataset: Path, extension: str, vendor: str
) -> None:
    from croissant_baker.handlers.registry import select_handler

    name = f"slide{extension}"
    path = write_wrapped(dataset, name, wsi_bytes(vendor))

    selection = select_handler(path, Path(name))

    assert type(selection.handler).__name__ == "WSIHandler"


def test_a_slide_dataset_bakes_into_a_document_mlcroissant_validates(
    dataset: Path,
) -> None:
    """The one thing no unit test can show: the nodes pass the validator.

    A record set mixing an ``sc:ImageObject`` extract with scalar fields over
    the same FileSet is new here, and so is a ``filename`` file property.
    """
    from croissant_baker.metadata_generator import MetadataGenerator

    for extension, vendor in VENDOR_EXTENSIONS.items():
        write_wrapped(dataset, f"slide{extension}", wsi_bytes(vendor))
    output = dataset / "croissant.jsonld"

    MetadataGenerator(
        str(dataset),
        name="slides",
        description="One synthetic slide per vendor",
        creators=[{"name": "Tester"}],
        date_published="2024-01-01",
    ).save_metadata(str(output), validate=True)

    assert output.exists()


def test_a_baked_slide_dataset_describes_every_slide_once(dataset: Path) -> None:
    from tests.helpers import bake, file_sets, record_sets

    for extension, vendor in VENDOR_EXTENSIONS.items():
        write_wrapped(dataset, f"slide{extension}", wsi_bytes(vendor))

    document = bake(dataset)

    (file_set,) = file_sets(document)
    (record_set,) = record_sets(document)
    assert file_set["@id"] == "wsi-files"
    assert record_set["@id"] == "slides"
    assert "aperio (1)" in record_set["description"]
