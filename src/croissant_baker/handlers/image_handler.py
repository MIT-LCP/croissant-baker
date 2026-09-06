"""Image file handler for datasets containing images."""

import logging
from pathlib import Path
from typing import Dict, List

import mlcroissant as mlc

from croissant_baker.handlers import ome
from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.utils import ARRAY_SHAPE_UNKNOWN_1D
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

# Standard image extensions that Pillow handles natively.
_PILLOW_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".webp",
    ".ico",
}

# TIFF extensions, read through tifffile. ``.btf`` is BigTIFF: not a format so
# much as classic TIFF's 64-bit offset field, which any writer switches to at
# 4 GiB — the size whole-slide histopathology, EM volumes, geospatial rasters
# and light-sheet stacks all cross.
_TIFF_EXTENSIONS = {".tiff", ".tif", ".btf"}

# All supported image extensions.
SUPPORTED_EXTENSIONS = _PILLOW_EXTENSIONS | _TIFF_EXTENSIONS

# MIME types for common image formats.
_MIME_TYPES: Dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    # BigTIFF has no registration of its own and is served as image/tiff.
    ".btf": "image/tiff",
}

# Magic-byte signatures for every supported image extension. These are
# stable, format-spec-defined headers — they don't change across versions:
#
#   PNG     : ISO/IEC 15948 §5.2 — 8-byte signature
#   JPEG    : ITU-T T.81 / JFIF  — SOI marker 0xFFD8 followed by another marker (0xFF**)
#   GIF     : GIF89a spec        — ASCII "GIF87a" or "GIF89a"
#   TIFF    : TIFF 6.0 §2        — "II\x2a\x00" (LE) or "MM\x00\x2a" (BE);
#                                  BigTIFF (Adobe ext, 2007) uses version byte
#                                  0x2b instead of 0x2a — read by Pillow and tifffile
#   BMP     : BITMAPFILEHEADER   — bfType "BM"
#   WebP    : RFC 6386           — "RIFF" + 4-byte size + "WEBP"
#   ICO     : Microsoft ICONDIR  — reserved 0x0000, type 0x0001 (icon) or 0x0002 (cursor)
#
# Each entry maps an extension to a predicate over the file's leading bytes.
# We read just enough bytes to satisfy the longest signature (WebP, 12 bytes).
_IMAGE_MAGIC_PREFIX_BYTES = 12

# Standard TIFF (version 0x2a) and BigTIFF (version 0x2b), little- and big-endian.
_TIFF_MAGICS = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")

_IMAGE_MAGIC_CHECKS = {
    ".png": lambda h: h.startswith(b"\x89PNG\r\n\x1a\n"),
    ".jpg": lambda h: h.startswith(b"\xff\xd8\xff"),
    ".jpeg": lambda h: h.startswith(b"\xff\xd8\xff"),
    ".gif": lambda h: h.startswith((b"GIF87a", b"GIF89a")),
    ".tiff": lambda h: h.startswith(_TIFF_MAGICS),
    ".tif": lambda h: h.startswith(_TIFF_MAGICS),
    ".btf": lambda h: h.startswith(_TIFF_MAGICS),
    ".bmp": lambda h: h.startswith(b"BM"),
    ".webp": lambda h: h[:4] == b"RIFF" and h[8:12] == b"WEBP",
    ".ico": lambda h: h[:4] in (b"\x00\x00\x01\x00", b"\x00\x00\x02\x00"),
}


def _has_image_magic(source: FileSource) -> bool:
    """Return True iff the leading bytes match the magic for the extension.

    Enforces the registry contract for :meth:`ImageHandler.claims`:
    if a handler claims a file, ``extract_metadata`` must be able to read it.
    A file with an image extension but non-image content (e.g. a renamed HTML
    page saved as ``.png``) is rejected and a WARNING is logged so the user
    can see which files were skipped and why. Missing or unreadable files
    return False without logging (those are caller errors, not impostors).
    """
    check = _IMAGE_MAGIC_CHECKS.get(source.suffix)
    if check is None:
        return False
    head = source.peek(_IMAGE_MAGIC_PREFIX_BYTES)
    if not head:
        return False
    if check(head):
        return True
    # Debug, not warning: the generator names every undescribed file once
    # through a capped path, so warning here doubles it and escapes the cap.
    logger.debug(
        "Skipping %s: extension is %s but file content does not match the "
        "expected image magic bytes",
        source.relative_path,
        source.suffix,
    )
    return False


def _read_with_pillow(source: FileSource) -> Dict:
    """Read image metadata using Pillow (standard RGB/grayscale images)."""
    from PIL import Image

    with source.open() as stream, Image.open(stream) as img:
        width, height = img.size
        # mode → number of bands: L=1, LA=2, RGB=3, RGBA=4, CMYK=4, etc.
        num_bands = len(img.getbands())
        return {
            "width": width,
            "height": height,
            "num_bands": num_bands,
            "image_format": img.format or source.suffix.lstrip(".").upper(),
        }


def _read_with_tifffile(source: FileSource) -> Dict:
    """Read TIFF metadata, and the OME-XML header if the file carries one."""
    import tifffile

    with source.open() as stream, tifffile.TiffFile(stream) as tif:
        page = tif.pages[0]
        header = ome.read(tif)
        if header is not None and header.refusal:
            # The count reaches the document through the record-set
            # description, which is where a *described* file's partial refusal
            # has to live: the scan report clears reason and detail once a file
            # is described. This names the individual file for an application
            # that configures logging; the package handler is a NullHandler, so
            # the library writes to no terminal of its own.
            logger.warning(
                "%s: the OME-XML was not parsed (%s). The file is described "
                "as a plain TIFF.",
                source.relative_path,
                header.refusal,
            )
        # Prefer the TIFF tags, which describe the logical image dimensions
        # directly and are not affected by planar storage order.
        width = getattr(page, "imagewidth", None)
        height = getattr(page, "imagelength", None)
        num_bands = getattr(page, "samplesperpixel", None)

        if width is None or height is None or num_bands is None:
            # Some TIFF variants expose dimensions more reliably through axes.
            shape = getattr(page, "shape", ())
            axes = getattr(page, "axes", "") or ""
            shape_map = dict(zip(axes, shape)) if axes else {}

            if width is None:
                width = shape_map.get("X")
            if height is None:
                height = shape_map.get("Y")
            if num_bands is None:
                num_bands = shape_map.get("S")

        if width is None or height is None:
            raise ValueError(
                f"Unable to determine TIFF dimensions for {source.relative_path}"
            )

        if num_bands is None:
            num_bands = 1

        return {
            "width": int(width),
            "height": int(height),
            "num_bands": int(num_bands),
            # BigTIFF is a TIFF variant, and a second token here would add one
            # to the format breakdown that no consumer expects.
            "image_format": "TIFF",
            "ome": header,
        }


def _read_image_metadata(source: FileSource) -> Dict:
    """Read image dimensions and band count, through the backend for the format.

    tifffile is the reference TIFF reader in the scientific Python stack;
    Pillow is a display library that happens to open TIFFs. Pillow reports one
    band for a three-channel image because that is Pillow's model of a TIFF,
    decodes tag 270 as latin-1 so ``µm`` comes back mojibake, and opens none of
    the twelve-band rasters this repository already carries. So TIFF is read
    through tifffile alone, and every other extension keeps Pillow.
    """
    if source.suffix in _TIFF_EXTENSIONS:
        return _read_with_tifffile(source)
    return _read_with_pillow(source)


class ImageHandler(FileTypeHandler):
    """
    Handler for image files (JPEG, PNG, TIFF, BigTIFF, GIF, BMP, WebP).

    - Extracts dimensions (width, height), band count, and format
    - Uses Pillow for standard formats, tifffile for every TIFF
    - Reads the OME-XML header of an OME-TIFF, and describes those files as a
      collection of their own so their fields are not attributed to the rest
    - Computes SHA256 for reproducibility
    - Returns metadata with ``image_properties`` key for the builder
    """

    EXTENSIONS = (
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".ico",
        ".tiff",
        ".tif",
        ".btf",
    )
    FORMAT_NAME = "Images"
    FORMAT_DESCRIPTION = (
        "Dimensions, color mode, encoding format; OME-XML header fields"
    )

    def claims(self, source: FileSource) -> bool:
        if source.suffix not in SUPPORTED_EXTENSIONS:
            return False
        return _has_image_magic(source)

    def extract(self, source: FileSource, **kwargs) -> dict:
        if not source.exists:
            raise FileNotFoundError(f"Image file not found: {source.relative_path}")

        try:
            img_meta = _read_image_metadata(source)
        except Exception as e:
            raise ValueError(f"Failed to read image {source.relative_path}: {e}") from e

        mime_type = _MIME_TYPES.get(source.suffix, "application/octet-stream")

        meta = {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": mime_type,
            "image_properties": {
                "width": img_meta["width"],
                "height": img_meta["height"],
                "num_bands": img_meta["num_bands"],
                "image_format": img_meta["image_format"],
            },
        }
        if img_meta.get("ome") is not None:
            meta["ome"] = img_meta["ome"]
        return meta

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        # An empty batch has nothing to summarise; emitting a FileSet over
        # zero files would describe data that is not there.
        if not file_metas:
            return BuildResult([], [])

        # One handler covers every image extension, so ``images`` has one row
        # per image of any kind, and OME fields on that row would attribute a
        # pixel size to every PNG in the same directory. So the OME files are
        # described separately and the two collections partition the batch.
        ome_metas = [meta for meta in file_metas if meta.get("ome") is not None]
        plain_metas = [meta for meta in file_metas if meta.get("ome") is None]

        file_sets, record_sets = [], []
        if plain_metas:
            listed = {_extension(meta) for meta in ome_metas}
            file_sets.append(_image_file_set(plain_metas, listed))
            record_sets.append(_image_record_set(plain_metas))
        if ome_metas:
            file_sets.append(_ome_file_set(ome_metas))
            record_sets.append(_ome_record_set(ome_metas))

        return BuildResult(file_sets, record_sets)


def _extension(meta: Dict) -> str:
    return Path(meta["file_name"]).suffix.lower()


def _relative(meta: Dict) -> str:
    """The file's logical dataset-relative path, which a FileSet resolves."""
    return meta.get("relative_path", meta["file_name"])


def _formats(summary: Dict) -> str:
    return ", ".join(f"{fmt} ({n})" for fmt, n in summary["format_counts"].items())


def _dimensions(summary: Dict) -> str:
    w_lo, w_hi = summary["width_range"]
    h_lo, h_hi = summary["height_range"]
    if w_lo == w_hi and h_lo == h_hi:
        return f"{w_lo}x{h_lo}"
    return f"{w_lo}-{w_hi}x{h_lo}-{h_hi}"


def _image_file_set(file_metas: List[Dict], listed: set) -> mlc.FileSet:
    """The FileSet over every image that is not an OME-TIFF.

    An extension some OME file also uses is listed file by file, because a
    ``**/*.tif`` glob would re-admit the OME files beside it. Every other
    extension keeps its glob, so one OME file in a photo archive does not turn
    this into a list the length of the dataset.
    """
    summary = collect_image_summary(file_metas)
    patterns, exact = set(), []
    for meta in file_metas:
        extension = _extension(meta)
        if extension in listed:
            exact.append(_relative(meta))
        else:
            patterns.add(f"**/*{extension}")

    return mlc.FileSet(
        id="image-files",
        name="Image files",
        description=f"{summary['num_images']} image files ({_formats(summary)})",
        encoding_formats=sorted({meta["encoding_format"] for meta in file_metas}),
        includes=sorted(patterns) + sorted(exact),
    )


def _image_record_set(file_metas: List[Dict]) -> mlc.RecordSet:
    summary = collect_image_summary(file_metas)
    b_lo, b_hi = summary["num_bands_range"]
    bands_note = f", {b_lo}-{b_hi} bands" if b_hi > 4 else ""
    formats_str = _formats(summary)

    return mlc.RecordSet(
        id="images",
        name="images",
        description=(
            f"{summary['num_images']} images "
            f"({_dimensions(summary)}{bands_note}): {formats_str}"
        ),
        fields=[
            mlc.Field(
                id="images/image_content",
                name="image",
                description=(
                    f"Image content ({summary['num_images']} files, {formats_str})"
                ),
                data_types=["sc:ImageObject"],
                source=mlc.Source(
                    file_set="image-files",
                    extract=mlc.Extract(file_property="content"),
                ),
            )
        ],
    )


OME_FILE_SET_ID = "ome-image-files"
OME_RECORD_SET_ID = "ome_images"

#: Field name, Croissant type, description prefix, the attribute of
#: :class:`~croissant_baker.handlers.ome.OMEHeader` it reads, and how the
#: batch's values are summarised. A field is emitted only where some file in
#: the batch declares the attribute: ``PhysicalSizeX`` is optional in the
#: schema, and a field naming something no file declares is noise.
_OME_FIELDS = (
    ("ome_version", "sc:Text", "OME schema version", "version"),
    (
        "ome_image_count",
        "sc:Integer",
        "OME Image elements the file declares",
        "image_count",
    ),
    ("size_c", "sc:Integer", "OME Pixels/@SizeC; channels in Image[0]", "size_c"),
    ("size_z", "sc:Integer", "OME Pixels/@SizeZ; focal planes in Image[0]", "size_z"),
    ("size_t", "sc:Integer", "OME Pixels/@SizeT; timepoints in Image[0]", "size_t"),
    (
        "dimension_order",
        "sc:Text",
        "OME Pixels/@DimensionOrder; plane order in Image[0]",
        "dimension_order",
    ),
    (
        "pixel_type",
        "sc:Text",
        "OME Pixels/@Type; stored pixel type in Image[0]",
        "pixel_type",
    ),
    (
        "physical_size_x",
        "sc:Float",
        "OME Pixels/@PhysicalSizeX; pixel width in Image[0]",
        "physical_size_x",
    ),
    (
        "physical_size_y",
        "sc:Float",
        "OME Pixels/@PhysicalSizeY; pixel height in Image[0]",
        "physical_size_y",
    ),
    (
        "physical_size_unit",
        "sc:Text",
        "OME Pixels/@PhysicalSizeXUnit; unit of the physical sizes",
        "physical_size_unit",
    ),
)


def _observed(values: list) -> str:
    """What the batch holds: one value, a range of numbers, or a set of words.

    No ``Field.value`` is emitted anywhere, so this is where the numbers live.
    A field describes the whole batch, and one file's value would be a false
    statement about the others.
    """
    if all(isinstance(value, (int, float)) for value in values):
        low, high = min(values), max(values)
        return f"{low}" if low == high else f"{low}-{high}"
    return ", ".join(sorted({str(value) for value in values}))


def _ome_file_set(file_metas: List[Dict]) -> mlc.FileSet:
    return mlc.FileSet(
        id=OME_FILE_SET_ID,
        name="OME-TIFF files",
        # Listed rather than globbed, so a plain TIFF beside these is not
        # re-admitted. The generator resolves an exact path to the file as
        # stored, wrapper included.
        includes=sorted(_relative(meta) for meta in file_metas),
        encoding_formats=sorted({meta["encoding_format"] for meta in file_metas}),
        description=f"{len(file_metas)} OME-TIFF file(s)",
    )


def _ome_record_set(file_metas: List[Dict]) -> mlc.RecordSet:
    headers = [meta["ome"] for meta in file_metas]
    # A BinaryOnly document is a place-holder whose metadata is in a companion
    # file, and the schema forbids it any other content. Its zero images is a
    # fact about the stub, not about the image the file holds, so it is left
    # out of the aggregates rather than reported as the file's own.
    parsed = [
        header for header in headers if not header.refusal and not header.binary_only
    ]

    fields = [
        mlc.Field(
            id=f"{OME_RECORD_SET_ID}/image",
            name="image",
            description=f"Image content ({len(file_metas)} OME-TIFF file(s))",
            data_types=["sc:ImageObject"],
            # The only extract this record set emits, and the only field whose
            # content *is* the file's content. mlcroissant reads image/tiff
            # content as the decoded pixels, so the same extract on size_c
            # would ask a consumer to cast an image to an integer.
            source=mlc.Source(
                file_set=OME_FILE_SET_ID,
                extract=mlc.Extract(file_property="content"),
            ),
        )
    ]

    for name, data_type, prefix, attribute in _OME_FIELDS:
        values = [
            value
            for value in (getattr(header, attribute) for header in parsed)
            if value is not None and value != ""
        ]
        if not values:
            continue
        fields.append(
            mlc.Field(
                id=f"{OME_RECORD_SET_ID}/{name}",
                name=name,
                description=f"{prefix} ({_observed(values)})",
                data_types=[data_type],
                source=mlc.Source(file_set=OME_FILE_SET_ID),
            )
        )

    channels = [name for header in parsed for name in header.channel_names]
    if channels:
        fields.append(
            mlc.Field(
                id=f"{OME_RECORD_SET_ID}/channel_names",
                name="channel_names",
                description=(
                    "OME Channel/@Name; channel labels in Image[0] "
                    f"({_observed(channels)})"
                ),
                data_types=["sc:Text"],
                source=mlc.Source(file_set=OME_FILE_SET_ID),
                is_array=True,
                # One shared Field has one shape, and a batch may hold a
                # 3-channel and a 40-channel file.
                array_shape=ARRAY_SHAPE_UNKNOWN_1D,
            )
        )

    return mlc.RecordSet(
        id=OME_RECORD_SET_ID,
        name=OME_RECORD_SET_ID,
        description=_ome_description(file_metas, headers),
        fields=fields,
    )


def _ome_description(file_metas: List[Dict], headers: List) -> str:
    """What the rows are, and what was not read.

    A described file has nowhere else to record a partial refusal: the scan
    report clears the reason and the detail once a file is described.
    """
    summary = collect_image_summary(file_metas)
    total = len(file_metas)
    text = (
        f"{total} OME-TIFF file(s) ({_dimensions(summary)}): one row per file. "
        "A file may declare several images, and may be one file of a multi-file "
        "OME set, so the Pixels fields describe Image[0] of each file."
    )

    refused = [header.refusal for header in headers if header.refusal]
    if refused:
        text += (
            f" {len(refused)} of {total} carried an ImageDescription that was "
            f"not parsed: {'; '.join(sorted(set(refused)))}."
        )

    stubs = [header for header in headers if header.binary_only]
    if stubs:
        named = sorted({header.companion for header in stubs if header.companion})
        text += (
            f" {len(stubs)} of {total} are BinaryOnly place-holders whose "
            "metadata is in a companion file that is not read"
        )
        text += f": {', '.join(named)}." if named else "."
    return text


def collect_image_summary(image_metadata_list: List[Dict]) -> Dict:
    """
    Summarize a collection of image file metadata into aggregate stats.

    Used by the metadata generator to describe an image dataset at the
    RecordSet level (e.g., total images, dimension ranges, formats).

    Args:
        image_metadata_list: List of metadata dicts from ImageHandler.

    Returns:
        Summary dict with counts, dimension ranges, and format breakdown.
    """
    if not image_metadata_list:
        return {}

    widths = []
    heights = []
    bands = []
    formats: Dict[str, int] = {}

    processed_count = 0
    for i, meta in enumerate(image_metadata_list):
        props = meta.get("image_properties")
        if not props:
            logger.warning(
                "Skipping image entry %d: missing or incomplete image_properties", i
            )
            continue

        processed_count += 1
        width = props.get("width")
        height = props.get("height")
        num_bands = props.get("num_bands")
        fmt = props.get("image_format")

        if width is not None:
            widths.append(width)
        if height is not None:
            heights.append(height)
        if num_bands is not None:
            bands.append(num_bands)
        if fmt is not None:
            formats[fmt] = formats.get(fmt, 0) + 1

    return {
        "num_images": processed_count,
        "width_range": (min(widths), max(widths)) if widths else (0, 0),
        "height_range": (min(heights), max(heights)) if heights else (0, 0),
        "num_bands_range": (min(bands), max(bands)) if bands else (0, 0),
        "format_counts": formats,
    }
