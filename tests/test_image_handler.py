"""Tests for image file handler."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
import tifffile

from croissant_baker.handlers import ome
from croissant_baker.handlers.image_handler import (
    _IMAGE_MAGIC_CHECKS,
    _MIME_TYPES,
    _TIFF_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    ImageHandler,
    collect_image_summary,
)
from croissant_baker.handlers.registry import builtin_handlers
from croissant_baker.sources import FileSource, make_source

from tests.helpers import (
    OME_TIFF,
    PNG_1X1,
    WRAPPER_SUFFIXES,
    ome_bomb,
    ome_image,
    ome_xml,
    tiff_bytes,
    write_wrapped,
)


@pytest.fixture
def handler() -> ImageHandler:
    return ImageHandler()


# Minimal magic-byte stubs per supported extension. These are not full
# images — they only need enough bytes to satisfy claims()'s check.
_IMAGE_STUBS = {
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff\xe0",
    ".jpeg": b"\xff\xd8\xff\xe0",
    ".gif": b"GIF89a",
    ".bmp": b"BM\x00\x00\x00\x00",
    ".webp": b"RIFF\x00\x00\x00\x00WEBP",
    ".tiff": b"II*\x00",
    ".tif": b"MM\x00*",
    ".btf": b"II+\x00",
    ".ico": b"\x00\x00\x01\x00",
}


@pytest.mark.parametrize(
    "filename",
    [
        "photo.jpg",
        "photo.jpeg",
        "photo.JPG",  # case-insensitive suffix
        "scan.png",
        "scan.PNG",
        "frame.gif",
        "icon.bmp",
        "hero.webp",
        "satellite.tiff",
        "satellite.tif",
        "satellite.TIFF",
        "tissue.btf",
        "image.ico",
    ],
)
def test_can_handle_accepts_supported_extensions_with_magic(
    handler: ImageHandler, tmp_path: Path, filename: str
) -> None:
    """Files whose extension is supported AND whose content matches the
    extension's magic bytes are accepted."""
    p = tmp_path / filename
    p.write_bytes(_IMAGE_STUBS[p.suffix.lower()])
    assert handler.claims(make_source(p)) is True


def test_a_renamed_file_is_declined_at_debug(
    handler: ImageHandler, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The generator owns the user-facing warning, so the handler's note is
    debug — asserted, or a regression to WARNING doubles every skip."""
    impostor = tmp_path / "fake.png"
    impostor.write_bytes(b"<!DOCTYPE html><html></html>")

    with caplog.at_level("DEBUG", logger="croissant_baker.handlers.image_handler"):
        assert handler.claims(make_source(impostor)) is False

    assert [
        r
        for r in caplog.records
        if r.levelname == "DEBUG"
        and impostor.name in r.message
        and "magic bytes" in r.message
    ], caplog.records


@pytest.fixture
def glaucoma_image_path() -> Path:
    """Path to a sample JPG from the glaucoma fundus dataset."""
    p = (
        Path(__file__).parent
        / "data"
        / "input"
        / "glaucoma_fundus"
        / "Images"
        / "0_0.jpg"
    )
    if not p.exists():
        pytest.skip(f"Glaucoma fundus image not found at {p}")
    return p


def test_extract_metadata_jpg(handler: ImageHandler, glaucoma_image_path: Path) -> None:
    meta = handler.extract(make_source(glaucoma_image_path))

    assert meta["file_name"] == "0_0.jpg"
    assert meta["encoding_format"] == "image/jpeg"
    assert meta["file_size"] > 0
    assert len(meta["sha256"]) == 64

    props = meta["image_properties"]
    assert props["width"] > 0
    assert props["height"] > 0
    assert props["num_bands"] in (1, 3, 4)
    assert props["image_format"] == "JPEG"


@pytest.fixture
def satellite_tiff_path() -> Path:
    """Path to a sample TIFF from the satellite dataset."""
    p = (
        Path(__file__).parent
        / "data"
        / "input"
        / "satellite_public_health"
        / "images"
        / "5001"
        / "image_2016-01-03.tiff"
    )
    if not p.exists():
        pytest.skip(f"Satellite TIFF not found at {p}")
    return p


def test_extract_metadata_tiff(
    handler: ImageHandler, satellite_tiff_path: Path
) -> None:
    meta = handler.extract(make_source(satellite_tiff_path))

    assert meta["file_name"] == "image_2016-01-03.tiff"
    assert meta["encoding_format"] == "image/tiff"
    assert meta["file_size"] > 0
    assert len(meta["sha256"]) == 64

    props = meta["image_properties"]
    assert props["width"] > 0
    assert props["height"] > 0
    # Sentinel-2 images have 12 bands
    assert props["num_bands"] == 12
    assert props["image_format"] == "TIFF"


def test_extract_metadata_separate_planar_tiff(
    handler: ImageHandler, tmp_path: Path
) -> None:
    """Regression test for TIFFs whose band axis is stored first."""
    path = tmp_path / "separate_planar.tiff"
    tifffile.imwrite(
        str(path),
        np.zeros((12, 5, 7), dtype=np.uint8),
        photometric="minisblack",
        planarconfig="separate",
    )

    props = handler.extract(make_source(path))["image_properties"]

    assert (props["width"], props["height"]) == (7, 5)
    assert props["num_bands"] == 12
    assert props["image_format"] == "TIFF"


# --------------------------------------------------------------------------
# BigTIFF
# --------------------------------------------------------------------------

BIGTIFF = tiff_bytes(size=16, bigtiff=True)


def test_every_supported_extension_is_declared_typed_and_sniffed() -> None:
    """``_TIFF_MAGICS`` already accepted BigTIFF's version byte. The magic check
    is keyed by extension first, so being in three of these tables is no use."""
    assert set(ImageHandler.EXTENSIONS) == SUPPORTED_EXTENSIONS
    assert set(_MIME_TYPES) == SUPPORTED_EXTENSIONS
    assert set(_IMAGE_MAGIC_CHECKS) == SUPPORTED_EXTENSIONS
    # BigTIFF has no registration of its own and is served as image/tiff.
    assert {_MIME_TYPES[ext] for ext in _TIFF_EXTENSIONS} == {"image/tiff"}


@pytest.mark.parametrize(
    ("name", "payload", "claimed"),
    [
        ("tissue.btf", BIGTIFF, True),
        # #93: a writer that crosses 4 GiB keeps the .tiff name, so the magic
        # check is the only thing that can rescue the file.
        ("tissue.tiff", BIGTIFF, True),
        ("impostor.btf", PNG_1X1, False),
    ],
    ids=["the .btf spelling", "a BigTIFF named .tiff", "PNG bytes under .btf"],
)
def test_a_bigtiff_is_claimed_by_its_magic_not_its_name(
    handler: ImageHandler,
    tmp_path: Path,
    name: str,
    payload: bytes,
    claimed: bool,
) -> None:
    # BigTIFF differs from classic TIFF in one version byte, so a fixture
    # written as classic TIFF would let the claim pass for the wrong reason.
    assert BIGTIFF[:4] == b"II+\x00"
    path = tmp_path / name
    path.write_bytes(payload)
    source = make_source(path)

    assert handler.claims(source) is claimed
    if not claimed:
        return

    props = handler.extract(source)["image_properties"]

    assert (props["width"], props["height"]) == (16, 16)
    assert props["num_bands"] == 1
    # BigTIFF is a TIFF variant, and a second token here would land in the
    # format breakdown that the record-set description reports.
    assert props["image_format"] == "TIFF"


@pytest.mark.parametrize(
    "handler_name", sorted(type(h).__name__ for h in builtin_handlers())
)
def test_only_the_image_handler_claims_a_bigtiff(
    handler_name: str, tmp_path: Path
) -> None:
    """The shared exclusive-format sweep cannot reach this: ``.btf`` resolves to
    ImageHandler, and the sweep then writes that owner's first sample, which is
    ``pixel.png``. So no handler is ever asked about a ``.btf`` but here."""
    path = tmp_path / "tissue.btf"
    path.write_bytes(BIGTIFF)
    other = next(h for h in builtin_handlers() if type(h).__name__ == handler_name)

    claimed = other.claims(make_source(path))

    assert claimed is (handler_name == "ImageHandler")


@pytest.mark.parametrize("suffix", WRAPPER_SUFFIXES)
def test_a_wrapped_bigtiff_is_read_through_the_wrapper(
    handler: ImageHandler, dataset: Path, suffix: str
) -> None:
    """tifffile seeks to the end of a BigTIFF to reach its offsets, and on a
    compressed stream that is a decompression of the whole file. It has to
    work, and it is the dearest read this handler does."""
    path = write_wrapped(dataset, "tissue.btf", BIGTIFF, suffix)

    props = handler.extract(make_source(path, Path("tissue.btf")))["image_properties"]

    assert (props["width"], props["height"]) == (16, 16)


# --------------------------------------------------------------------------
# Which backend reads a TIFF
# --------------------------------------------------------------------------

PLAIN_TIFF = tiff_bytes()


@pytest.mark.parametrize(
    ("name", "payload", "forbidden"),
    [
        ("scan.tif", PLAIN_TIFF, "_read_with_pillow"),
        ("pixel.png", PNG_1X1, "_read_with_tifffile"),
    ],
    ids=["a TIFF never reaches Pillow", "a PNG never reaches tifffile"],
)
def test_each_format_reaches_only_its_own_backend(
    handler: ImageHandler,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    payload: bytes,
    forbidden: str,
) -> None:
    """Pillow opens some TIFFs and reads them worse — see
    ``_read_image_metadata``. Only the TIFF extensions move."""
    from croissant_baker.handlers import image_handler as module

    def fail(_source):
        raise AssertionError(f"{name} was read through {forbidden}")

    monkeypatch.setattr(module, forbidden, fail)
    path = tmp_path / name
    path.write_bytes(payload)

    assert handler.extract(make_source(path))["image_properties"]["width"] > 0


def test_an_unreadable_tiff_raises_a_value_error_naming_the_file(
    handler: ImageHandler, tmp_path: Path
) -> None:
    """The message becomes the reason detail a user reads in ``--report``. The
    shared garbage-bytes sweep writes this handler's first sample name, which
    is a PNG, so a broken TIFF is only covered here."""
    path = tmp_path / "truncated.tif"
    path.write_bytes(BIGTIFF[:20])

    with pytest.raises(ValueError, match="truncated.tif"):
        handler.extract(make_source(path))


# --------------------------------------------------------------------------
# No pixel data, at any size
# --------------------------------------------------------------------------

TILED_OME = tiff_bytes(ome_xml(ome_image()), planes=4, size=64, tile=(16, 16))


class ReadLog(io.BytesIO):
    """A stream that records the byte interval of every read it serves."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.intervals: list = []
        self.back_seeks = 0

    def read(self, size=-1):
        start = self.tell()
        chunk = super().read(size)
        self.intervals.append((start, start + len(chunk)))
        return chunk

    def seek(self, offset, whence=0):
        before = self.tell()
        position = super().seek(offset, whence)
        if position < before:
            self.back_seeks += 1
        return position


def pixel_intervals(data: bytes) -> list:
    """Where the pixels are, from the offsets and byte counts the TIFF declares."""
    out = []
    with tifffile.TiffFile(io.BytesIO(data)) as tif:
        for page in tif.pages:
            tags = page.tags
            offsets = tags.get("TileOffsets") or tags.get("StripOffsets")
            counts = tags.get("TileByteCounts") or tags.get("StripByteCounts")
            out += [(o, o + c) for o, c in zip(offsets.value, counts.value) if c]
    return out


def test_describing_a_tiled_ome_tiff_reads_no_pixel_data(
    handler: ImageHandler,
) -> None:
    """A byte cap would be the wrong assertion: the pull scales with the
    ImageDescription and the IFD count, so 8 KiB holds at 3 channels and
    breaches at 40. Overlap, not "no read starts at a pixel offset", which a
    read beginning earlier and spanning into one would satisfy."""
    log = ReadLog(TILED_OME)
    source = FileSource(
        name="tiled.ome.tif",
        relative_path=Path("tiled.ome.tif"),
        size=len(TILED_OME),
        exists=True,
        _open_binary=lambda: log,
        _digest=lambda: "0" * 64,
    )

    handler.extract(source)

    assert log.intervals, "nothing was read at all, so the check proves nothing"
    pixels = pixel_intervals(TILED_OME)
    assert pixels, "the fixture declares no pixel data to avoid"
    overlaps = [
        (read, pixel)
        for read in log.intervals
        for pixel in pixels
        if read[0] < pixel[1] and pixel[0] < read[1]
    ]
    assert not overlaps, overlaps
    # A backward seek on a wrapped file costs a decompression from offset 0.
    assert log.back_seeks <= 3, log.back_seeks


# --------------------------------------------------------------------------
# The OME header
# --------------------------------------------------------------------------
def test_an_ome_tiff_keeps_its_tiff_tags_alongside_its_header(
    handler: ImageHandler, tmp_path: Path
) -> None:
    """``num_bands`` is TIFF SamplesPerPixel and stays so. It is genuinely 1 for
    a three-channel OME stored as three IFDs, and ``size_c`` is the channel
    count — reporting one of them as the other would lose both."""
    path = tmp_path / "morphology.ome.tif"
    path.write_bytes(OME_TIFF)

    meta = handler.extract(make_source(path))

    assert meta["image_properties"]["num_bands"] == 1
    assert meta["ome"].size_c == 3


BOMB_TIFF = tiff_bytes(ome_bomb())
OVERSIZED_TIFF = tiff_bytes(ome_xml(f"<!--{'x' * (ome.MAX_DESCRIPTION_BYTES + 1)}-->"))


@pytest.mark.parametrize(
    ("payload", "warnings"),
    [
        (BOMB_TIFF, 1),
        (OVERSIZED_TIFF, 1),
        # Closed at the root, so tifffile still calls it OME, but not
        # well-formed. A description truncated before ``</OME>`` is a different
        # case: nothing identifies it as OME, so it is not refused.
        (tiff_bytes(ome_xml("<Image>")), 1),
        # Or every microscopy bake would warn on every file.
        (OME_TIFF, 0),
    ],
    ids=["entity declaration", "oversized", "malformed", "sound"],
)
def test_only_a_refused_description_is_warned_about(
    handler: ImageHandler,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    payload: bytes,
    warnings: int,
) -> None:
    """The count reaches the document through the record-set description, which
    is where a described file's partial refusal has to live. This names the one
    file, for an application that configures logging — the library itself
    carries a NullHandler and writes to no terminal."""
    path = tmp_path / "a.ome.tif"
    path.write_bytes(payload)

    with caplog.at_level("DEBUG", logger="croissant_baker.handlers"):
        handler.extract(make_source(path))

    logged = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(logged) == warnings, caplog.records
    assert all("a.ome.tif" in r.getMessage() for r in logged)


# --------------------------------------------------------------------------
# The OME collection
# --------------------------------------------------------------------------

IMAGEJ_TIFF = tiff_bytes("ImageJ=1.53t\nimages=1\nslices=1\n")
BINARY_ONLY_TIFF = tiff_bytes(
    ome_xml('<BinaryOnly UUID="urn:uuid:9c1b" MetadataFile="plate.companion.ome"/>')
)
OME_40 = tiff_bytes(
    ome_xml(
        ome_image(
            pixels='DimensionOrder="XYCZT" Type="uint8" SizeX="8" SizeY="8"'
            ' SizeC="40" SizeZ="1" SizeT="1"',
            channels=("CD3", "CD8"),
        )
    )
)
OME_NO_PHYSICAL_SIZE = tiff_bytes(
    ome_xml(
        ome_image(
            pixels='DimensionOrder="XYCZT" Type="uint16" SizeX="8" SizeY="8"'
            ' SizeC="3" SizeZ="1" SizeT="1"'
        )
    )
)
OME_TWO_IMAGES = tiff_bytes(
    ome_xml(ome_image() + ome_image(identifier="Image:1", channels=("CD3",)))
)
OME_NAMED = tiff_bytes(
    ome_xml(
        ome_image(attrs=' Name="Patient 3 slide 2"'),
        attrs=' UUID="urn:uuid:9c1bde0e-dead-beef" Creator="Acme Scanner 4.2"',
    )
)


def ome_partner(other: str) -> bytes:
    """One file of a multi-file OME set, naming its partner the way OME does."""
    return tiff_bytes(
        ome_xml(
            ome_image(
                trailing=f'<TiffData IFD="0"><UUID FileName="{other}">'
                "urn:uuid:9c1b</UUID></TiffData>"
            )
        )
    )


def described(handler: ImageHandler, directory: Path, files: dict) -> tuple:
    """Write ``files``, extract each, and stamp what the generator stamps."""
    metas = []
    for name, payload in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        meta = handler.extract(make_source(path, Path(name)))
        meta["relative_path"] = name
        metas.append(meta)
    return metas, [f"file_{i}" for i in range(len(metas))]


def build(handler: ImageHandler, directory: Path, files: dict):
    return handler.build_croissant(*described(handler, directory, files))


def nodes_by_name(nodes) -> dict:
    return {node.name: node for node in nodes}


def fields_of(record_set) -> dict:
    return {field["name"]: field for field in record_set.to_json().get("field", [])}


def as_json(result) -> str:
    """Everything the handler contributes to the document, as one string."""
    import json

    return json.dumps(
        [node.to_json() for node in (*result.file_sets, *result.record_sets)],
        ensure_ascii=False,
    )


def test_a_batch_with_no_ome_file_describes_one_collection(
    handler: ImageHandler, dataset: Path
) -> None:
    """The gate on every corpus already committed: no OME file, no change."""
    result = build(
        handler, dataset, {"a.png": PNG_1X1, "b.tif": PLAIN_TIFF, "c.btf": BIGTIFF}
    )

    assert [fs.id for fs in result.file_sets] == ["image-files"]
    assert [rs.name for rs in result.record_sets] == ["images"]
    assert sorted(result.file_sets[0].includes) == ["**/*.btf", "**/*.png", "**/*.tif"]


def resolve(includes, directory: Path) -> set:
    found = set()
    for pattern in includes:
        if "*" in pattern:
            found |= {str(p.relative_to(directory)) for p in directory.glob(pattern)}
        else:
            found.add(pattern)
    return found


def test_the_two_collections_partition_the_batch(
    handler: ImageHandler, dataset: Path
) -> None:
    """The OME files leave ``images``, an existing public record set, so every
    image must still land in exactly one of the two collections.

    Asserted as exact include lists rather than by membership: the three rules
    that decide them — list a shared extension, glob every other, name an OME
    file by its dataset-relative path — are all invisible to a set comparison.
    """
    files = {
        "a.ome.tif": OME_TIFF,
        "nested/b.ome.tif": OME_40,
        "plain.tif": PLAIN_TIFF,
        # Tag 270 carries all sorts of things. Only OME-XML is OME.
        "imagej.tif": IMAGEJ_TIFF,
        "photo.png": PNG_1X1,
        "tissue.btf": BIGTIFF,
    }

    result = build(handler, dataset, files)

    by_name = nodes_by_name(result.file_sets)
    assert sorted(fs.id for fs in result.file_sets) == [
        "image-files",
        "ome-image-files",
    ]
    assert sorted(rs.name for rs in result.record_sets) == ["images", "ome_images"]
    assert by_name["OME-TIFF files"].includes == ["a.ome.tif", "nested/b.ome.tif"]
    assert by_name["Image files"].includes == [
        "**/*.btf",
        "**/*.png",
        "imagej.tif",
        "plain.tif",
    ]
    assert list(fields_of(nodes_by_name(result.record_sets)["images"])) == ["image"]

    plain, ome_files = (
        resolve(by_name[name].includes, dataset)
        for name in ("Image files", "OME-TIFF files")
    )
    assert plain & ome_files == set()
    assert plain | ome_files == set(files)


def test_every_field_is_typed_and_only_the_image_field_extracts(
    handler: ImageHandler, dataset: Path
) -> None:
    """mlcroissant's ``fileProperty: content`` for ``image/tiff`` is the decoded
    pixels, so putting that extract on ``size_c`` would ask a consumer to cast
    an image to an integer."""
    result = build(handler, dataset, {"a.ome.tif": OME_TIFF})

    fields = fields_of(nodes_by_name(result.record_sets)["ome_images"])
    # Exact, so that dropping a row from ``_OME_FIELDS`` fails here. Spot-checking
    # four of the types let three fields go missing in silence. ``str`` because
    # mlcroissant hands a dataType back as an rdflib URIRef, which does not
    # compare equal to a plain str from the left.
    assert {name: str(f["dataType"]) for name, f in fields.items()} == {
        "image": "sc:ImageObject",
        "ome_version": "sc:Text",
        "ome_image_count": "sc:Integer",
        "size_c": "sc:Integer",
        "size_z": "sc:Integer",
        "size_t": "sc:Integer",
        "dimension_order": "sc:Text",
        "pixel_type": "sc:Text",
        "physical_size_x": "sc:Float",
        "physical_size_y": "sc:Float",
        "physical_size_unit": "sc:Text",
        "channel_names": "sc:Text",
    }
    assert fields["channel_names"]["cr:isArray"] is True
    assert fields["channel_names"]["cr:arrayShape"] == "-1"

    assert fields["image"]["source"]["extract"] == {"fileProperty": "content"}
    for name, field in fields.items():
        assert "value" not in field, name
        assert field["source"]["fileSet"] == {"@id": "ome-image-files"}
        if name != "image":
            assert "extract" not in field["source"], name


def test_each_field_describes_what_the_whole_batch_holds(
    handler: ImageHandler, dataset: Path
) -> None:
    """One shared field describes the whole batch, so one file's value would be
    a false statement about the rest.

    Exact, not substring: a range that never collapses reads ``3-3`` and still
    contains ``3``, and a set joined in hash order still contains every word.
    """
    result = build(handler, dataset, {"a.ome.tif": OME_TIFF, "b.ome.tif": OME_40})

    record_set = nodes_by_name(result.record_sets)["ome_images"]
    fields = fields_of(record_set)
    assert {name: f["description"] for name, f in fields.items()} == {
        "image": "Image content (2 OME-TIFF file(s))",
        "ome_version": "OME schema version (2016-06)",
        "ome_image_count": "OME Image elements the file declares (1)",
        "size_c": "OME Pixels/@SizeC; channels in Image[0] (3-40)",
        "size_z": "OME Pixels/@SizeZ; focal planes in Image[0] (1)",
        "size_t": "OME Pixels/@SizeT; timepoints in Image[0] (1-5)",
        "dimension_order": (
            "OME Pixels/@DimensionOrder; plane order in Image[0] (XYCZT)"
        ),
        "pixel_type": "OME Pixels/@Type; stored pixel type in Image[0] (uint16, uint8)",
        "physical_size_x": (
            "OME Pixels/@PhysicalSizeX; pixel width in Image[0] (0.2125)"
        ),
        "physical_size_y": (
            "OME Pixels/@PhysicalSizeY; pixel height in Image[0] (0.425)"
        ),
        "physical_size_unit": (
            "OME Pixels/@PhysicalSizeXUnit; unit of the physical sizes (µm)"
        ),
        "channel_names": (
            "OME Channel/@Name; channel labels in Image[0] "
            "(18S, ATP1A1, CD3, CD8, DAPI)"
        ),
    }
    assert record_set.description.startswith("2 OME-TIFF file(s) (8x8): ")


def test_a_field_no_file_declares_is_not_emitted(
    handler: ImageHandler, dataset: Path
) -> None:
    """``PhysicalSizeX`` is optional in the schema, and a field naming
    something no file declares is noise."""
    result = build(handler, dataset, {"a.ome.tif": OME_NO_PHYSICAL_SIZE})

    fields = fields_of(nodes_by_name(result.record_sets)["ome_images"])
    assert "physical_size_x" not in fields
    assert "physical_size_unit" not in fields
    assert "size_c" in fields


@pytest.mark.parametrize(
    ("files", "images_declared"),
    [
        # One document may declare several images: a multi-position acquisition
        # does.
        ({"a.ome.tif": OME_TWO_IMAGES}, "2"),
        # And one logical image may be spread over several files. Grouping
        # those is a separate change; reporting rows as images is not.
        (
            {
                "a.ome.tif": ome_partner("b.ome.tif"),
                "b.ome.tif": ome_partner("a.ome.tif"),
            },
            "1",
        ),
    ],
    ids=["two images in one file", "two files cross-referencing"],
)
def test_the_record_set_says_its_rows_are_files(
    handler: ImageHandler,
    dataset: Path,
    files: dict,
    images_declared: str,
) -> None:
    """A FileSet yields one record per file, so the count makes the gap visible
    instead of leaving a consumer to assume rows are images."""
    result = build(handler, dataset, files)

    record_set = nodes_by_name(result.record_sets)["ome_images"]
    fields = fields_of(record_set)
    assert fields["ome_image_count"]["description"].endswith(f"({images_declared})")
    assert f"{len(files)} OME-TIFF file(s)" in record_set.description
    assert "one row per file" in record_set.description
    assert "Image[0]" in record_set.description


def test_channel_names_are_the_only_vocabulary_that_reaches_the_document(
    handler: ImageHandler, dataset: Path
) -> None:
    """The schema defines ``Channel/@Name`` as an acquisition channel's label,
    so it names an antibody or a fluorophore. It says nothing about
    ``Image/@Name``, which in practice holds slide labels and operator notes."""
    result = build(handler, dataset, {"a.ome.tif": OME_NAMED})

    fields = fields_of(nodes_by_name(result.record_sets)["ome_images"])
    channels = fields["channel_names"]["description"]
    document = as_json(result)
    for name in ("DAPI", "ATP1A1", "18S"):
        assert name in channels
        assert document.count(name) == 1, f"{name} reached a node of its own"
    for secret in ("Patient 3 slide 2", "Acme Scanner 4.2", "9c1bde0e-dead-beef"):
        assert secret not in document


def test_a_refused_description_is_counted_and_never_expanded(
    handler: ImageHandler, dataset: Path
) -> None:
    """``ScanEntry.describe()`` clears the reason and the detail, so a described
    file has nowhere else to record a partial refusal."""
    result = build(handler, dataset, {"a.ome.tif": BOMB_TIFF, "b.ome.tif": OME_TIFF})

    record_set = nodes_by_name(result.record_sets)["ome_images"]
    fields = fields_of(record_set)
    assert "1 of 2" in record_set.description
    assert "not parsed" in record_set.description
    # The refused file contributed nothing, and the sound one still did.
    assert fields["size_c"]["description"].endswith("(3)")
    assert "lol" not in as_json(result)


def test_a_binary_only_file_names_its_companion_and_declares_nothing(
    handler: ImageHandler, dataset: Path
) -> None:
    """The schema forbids a place-holder any other content, so it carries no
    header field — not even the zero images it declares, which is a fact about
    the stub rather than about the image the file holds."""
    result = build(handler, dataset, {"a.ome.tif": BINARY_ONLY_TIFF})

    record_set = nodes_by_name(result.record_sets)["ome_images"]
    assert "plate.companion.ome" in record_set.description
    assert list(fields_of(record_set)) == ["image"]


# --------------------------------------------------------------------------
# The batch summary
# --------------------------------------------------------------------------


def test_collect_image_summary() -> None:
    metas = [
        {
            "image_properties": {
                "width": 100,
                "height": 200,
                "num_bands": 3,
                "image_format": "JPEG",
            }
        },
        {
            "image_properties": {
                "width": 640,
                "height": 480,
                "num_bands": 3,
                "image_format": "JPEG",
            }
        },
        {
            "image_properties": {
                "width": 256,
                "height": 256,
                "num_bands": 12,
                "image_format": "TIFF",
            }
        },
    ]
    summary = collect_image_summary(metas)

    assert summary["num_images"] == 3
    assert summary["width_range"] == (100, 640)
    assert summary["height_range"] == (200, 480)
    assert summary["num_bands_range"] == (3, 12)
    assert summary["format_counts"] == {"JPEG": 2, "TIFF": 1}


def test_the_format_breakdown_does_not_follow_discovery_order() -> None:
    """Read into a description verbatim, and discovery order is rglob's, so a
    dataset and the same dataset compressed would describe one batch two ways.
    Every committed image corpus holds a single format, so no golden can catch
    this; only a mixed batch, which is what an OME dataset is.
    """
    tiff_first = [_img_meta("a.tif", fmt="TIFF"), _img_meta("b.png", fmt="PNG")]
    png_first = list(reversed(tiff_first))

    assert (
        list(collect_image_summary(tiff_first)["format_counts"])
        == list(collect_image_summary(png_first)["format_counts"])
        == ["PNG", "TIFF"]
    )


def _img_meta(name, fmt="JPEG", mime="image/jpeg", w=100, h=100, bands=3):
    return {
        "file_name": name,
        "encoding_format": mime,
        "image_properties": {
            "width": w,
            "height": h,
            "num_bands": bands,
            "image_format": fmt,
        },
    }


def test_image_build_croissant(handler: ImageHandler) -> None:
    metas = [_img_meta("a.jpg"), _img_meta("b.jpg")]
    filesets, record_sets = handler.build_croissant(metas, ["file_0", "file_1"])

    assert len(filesets) == 1
    assert len(record_sets) == 1
    assert record_sets[0].name == "images"
    assert "**/*.jpg" in filesets[0].includes


def test_image_build_croissant_multiband(handler: ImageHandler) -> None:
    metas = [
        _img_meta(f"tile_{i}.tif", fmt="TIFF", mime="image/tiff", bands=12)
        for i in range(3)
    ]
    _, record_sets = handler.build_croissant(metas, [f"file_{i}" for i in range(3)])

    assert "band" in record_sets[0].description
