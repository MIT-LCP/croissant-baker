"""Shared test vocabulary: sample data, fixture writing, baking, navigation."""

from __future__ import annotations

import base64
import gzip
import io
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import tifffile

from typer.testing import CliRunner

from croissant_baker import compression
from croissant_baker.__main__ import app
from croissant_baker.handlers.base_handler import FileTypeHandler
from croissant_baker.handlers.registry import HandlerRegistry, builtin_handlers
from croissant_baker.metadata_generator import MetadataGenerator
from croissant_baker.scan import ScanReport

DATA = Path(__file__).parent / "data" / "input"
_SPECT = DATA / "spect_demo"


def _csv() -> list:
    return [("data.csv", b"id,name,score\n1,Ada,9.5\n2,Grace,9.9\n")]


def _tsv() -> list:
    return [("data.tsv", b"id\tname\tscore\n1\tAda\t9.5\n2\tGrace\t9.9\n")]


def _jsonl() -> list:
    return [
        ("records.jsonl", b'{"id": 1, "name": "Ada"}\n{"id": 2, "name": "Grace"}\n')
    ]


def _ndjson() -> list:
    """Three bulk-export chunks, which is how FHIR data actually arrives."""
    return [
        (
            f"Patient.{i:03d}.ndjson",
            b'{"resourceType": "Patient", "id": "a", "gender": "female"}\n'
            b'{"resourceType": "Patient", "id": "b", "gender": "male"}\n',
        )
        for i in range(3)
    ]


def _parquet() -> list:
    buffer = io.BytesIO()
    pq.write_table(
        pa.table({"id": pa.array([1, 2]), "name": pa.array(["Ada", "Grace"])}), buffer
    )
    return [("table.parquet", buffer.getvalue())]


PNG_1X1 = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQD"
    b"wAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


#: The namespace of the current OME schema. It is versioned, so nothing in
#: the source matches this constant — the version is read off the root element.
OME_NAMESPACE = "http://www.openmicroscopy.org/Schemas/OME/2016-06"


def ome_xml(body: str, *, namespace: str = OME_NAMESPACE, attrs: str = "") -> str:
    """An OME-XML document wrapping ``body``, shaped the way a writer emits one."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<OME xmlns="{namespace}"{attrs}>{body}</OME>'
    )


def tiff_bytes(
    description: Optional[str] = None, *, planes: int = 1, size: int = 8, **kwargs
) -> bytes:
    """A TIFF in memory, with ``description`` written verbatim to tag 270.

    Encoded as UTF-8 because tag 270 is nominally 7-bit ASCII and every OME
    writer puts ``µm`` in it regardless.
    """
    shape = (planes, size, size) if planes > 1 else (size, size)
    buffer = io.BytesIO()
    tifffile.imwrite(
        buffer,
        np.zeros(shape, np.uint16),
        photometric="minisblack",
        description=None if description is None else description.encode("utf-8"),
        # Otherwise tifffile writes its own shape note into tag 270, and a
        # fixture meant to carry no description carries one.
        metadata=None,
        **kwargs,
    )
    return buffer.getvalue()


#: The ``<Pixels>`` attributes a microscope writes, on an 8x8 fixture.
#:
#: Every pair the handler could confuse holds two different values: SizeZ
#: against SizeT, PhysicalSizeX against Y, and the X unit against the Y unit.
#: Each axis retains its own unit. With the pairs equal
#: — as a symmetric fixture makes them — a field reading its neighbour is
#: invisible, and three such swaps went unnoticed.
OME_PIXELS = (
    'DimensionOrder="XYCZT" Type="uint16" SizeX="8" SizeY="8"'
    ' SizeC="3" SizeZ="1" SizeT="5"'
    ' PhysicalSizeX="0.2125" PhysicalSizeXUnit="µm"'
    ' PhysicalSizeY="0.425" PhysicalSizeYUnit="mm"'
)


def ome_image(
    *,
    identifier: str = "Image:0",
    attrs: str = "",
    pixels: str = OME_PIXELS,
    channels: tuple = ("DAPI", "ATP1A1", "18S"),
    trailing: str = "",
) -> str:
    """One ``<Image>`` element, the shape Bio-Formats and tifffile both write."""
    inner = "".join(
        f'<Channel ID="Channel:0:{i}" SamplesPerPixel="1" Name="{name}"/>'
        for i, name in enumerate(channels)
    )
    return (
        f'<Image ID="{identifier}"{attrs}>'
        f'<Pixels ID="Pixels:0" {pixels}>{inner}{trailing}</Pixels>'
        "</Image>"
    )


def ome_bomb(levels: int = 6) -> str:
    """A billion-laughs OME-XML document of ``levels`` entity generations.

    Six is deliberate. Expat 2.4+ caps input amplification, so a nine-level
    bomb raises ``ParseError`` unaided — a parser with no declaration check at
    all would survive one and prove nothing. Six still expands.
    """
    entities = ['<!ENTITY a0 "lol">']
    entities += [f'<!ENTITY a{i} "{"&a%d;" % (i - 1) * 10}">' for i in range(1, levels)]
    return (
        '<?xml version="1.0"?>\n<!DOCTYPE OME [\n'
        + "\n".join(entities)
        + f']>\n<OME xmlns="{OME_NAMESPACE}"><Image ID="&a{levels - 1};"/></OME>'
    )


#: A three-channel OME-TIFF, written by hand rather than by ``imwrite(ome=True)``
#: so the bytes are the same on every run — that writer stamps a fresh UUID.
OME_TIFF = tiff_bytes(ome_xml(ome_image()), planes=3)


# Whole-slide images
#
# One synthetic slide per vendor, small enough to build in memory on every
# run. Each carries the signal tifffile identifies that vendor by, and
# ``tests/test_wsi.py`` asserts the corresponding ``is_*`` property before any
# other test relies on it.


def _rgb(width: int, height: int) -> np.ndarray:
    """One RGB plane. Zeros, so a deflated page costs a few hundred bytes."""
    return np.zeros((height, width, 3), np.uint8)


APERIO_HEADER = "Aperio Image Library v12.0.15"

#: What an Aperio scanner writes into tag 270: a two-line header, then
#: pipe-separated ``key = value`` items. The dimensions are the fixture's own,
#: so nothing in the file contradicts anything else in it.
APERIO_DESCRIPTION = (
    f"{APERIO_HEADER}\r\n256x256 [0,0 256x256] (128x128) JPEG/RGB Q=30"
    "|AppMag = 20|StripeWidth = 2040|ScanScope ID = CPAPERIOCS"
    "|MPP = 0.4990|Left = 25.7|Top = 23.4"
)

#: A Leica SCN document, cut down to the elements a slide always carries.
#: The root element decides the format: tifffile calls a page SCN when its
#: description ends in ``</scn>``.
SCN_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<scn xmlns="http://www.leica-microsystems.com/scn/2010/10/01">'
    '<collection name="collection" sizeX="256" sizeY="256">'
    '<image name="Image1">'
    "<scanSettings><objectiveSettings><objective>40</objective>"
    "</objectiveSettings></scanSettings>"
    '<pixels sizeX="256" sizeY="256">'
    '<dimension sizeX="256" sizeY="256" r="0" ifd="0"/>'
    '<dimension sizeX="128" sizeY="128" r="1" ifd="1"/>'
    "</pixels>"
    '<view sizeX="64000" sizeY="64000" offsetX="0" offsetY="0"/>'
    "</image></collection></scn>"
)

#: The root element of an SCN document, which is what tifffile calls the
#: format by: a page is SCN when its description ends in ``</scn>``.
SCN_ROOT = '<scn xmlns="http://www.leica-microsystems.com/scn/2010/10/01">'

#: One entity declaration is enough: the refusal is on the declaration itself,
#: not on how far the expansion would have got. Beside :func:`ome_bomb`,
#: which is the same refusal in the other XML the repository parses.
SCN_BOMB = (
    '<?xml version="1.0"?>\n<!DOCTYPE scn [\n<!ENTITY a "lol">\n]>\n'
    f'{SCN_ROOT}<collection name="&a;"/></scn>'
)

#: An SCN document whose collection element is never closed.
SCN_MALFORMED = f"{SCN_ROOT}<collection></scn>"

#: The XMP packet a Ventana scanner puts in tag 700. ``ScanRes`` is microns
#: per pixel and ``Magnification`` the objective power.
VENTANA_XMP = (
    '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    '<iScan Magnification="40" ScanRes="0.2500" Z="0"/>'
    "</x:xmpmeta><?xpacket end='w'?>"
).encode("utf-8")

#: The XML an Akoya scanner writes into tag 270, trimmed to the elements that
#: describe the optics. The scan profile is a large opaque blob in a real file.
QPI_XML = (
    "<PerkinElmer-QPI-ImageDescription>"
    "<DescriptionVersion>2</DescriptionVersion>"
    "<ImageType>FullResolution</ImageType>"
    "<Name>DAPI</Name>"
    "<Objective>20x</Objective>"
    "<ScanProfile>{}</ScanProfile>"
    "</PerkinElmer-QPI-ImageDescription>"
)


def aperio_bytes(
    description: Optional[str] = None, *, tiled_label: bool = False
) -> bytes:
    """An Aperio SVS, in the page order tifffile's SVS series builder assumes.

    Base, thumbnail, one further level, label, macro. The thumbnail sits at
    page 1 whatever it holds, so a fixture that omits it hands page 1 to the
    builder as the thumbnail and loses a pyramid level.

    ``tiled_label`` stores the label in tiles rather than strips, which real
    Ventana and Leica scanners do: a page walk then finds a page that looks
    like a level, and only the vendor series tells the two apart.
    """
    buffer = io.BytesIO()
    plane = {"photometric": "rgb", "metadata": None}
    tiled = {**plane, "tile": (128, 128), "compression": "deflate"}
    label = {**tiled, "tile": (16, 16)} if tiled_label else plane
    with tifffile.TiffWriter(buffer) as writer:
        writer.write(
            _rgb(256, 256),
            description=APERIO_DESCRIPTION if description is None else description,
            **tiled,
        )
        writer.write(
            _rgb(64, 64),
            description=f"{APERIO_HEADER}\r\n256x256 -> 64x64 - |AppMag = 20",
            **plane,
        )
        writer.write(
            _rgb(128, 128),
            description=f"{APERIO_HEADER}\r\n256x256 -> 128x128 - |AppMag = 20",
            **tiled,
        )
        writer.write(
            _rgb(32, 32),
            subfiletype=1,
            description=f"{APERIO_HEADER}\r\nlabel 32x32",
            **label,
        )
        writer.write(
            _rgb(48, 48),
            subfiletype=9,
            description=f"{APERIO_HEADER}\r\nmacro 48x48",
            **plane,
        )
    return buffer.getvalue()


def hamamatsu_bytes(*, mpp: float = 0.46, objective: float = 20.0) -> bytes:
    """A Hamamatsu NDPI: tags 65420 and 271, and a resolution in centimetres.

    Written big-endian. tifffile decides a little-endian classic TIFF named
    ``.ndpi`` has 64-bit IFD offsets, which a real NDPI does and this
    synthetic one does not, and then finds no page in it at all. A
    big-endian file never takes that branch, and no real NDPI is big-endian,
    so nothing else in the suite is misled by the choice.
    """
    buffer = io.BytesIO()
    tifffile.imwrite(
        buffer,
        _rgb(64, 64),
        photometric="rgb",
        metadata=None,
        byteorder=">",
        compression="deflate",
        resolution=(10000 / mpp, 10000 / mpp),
        resolutionunit="CENTIMETER",
        extratags=[
            (65420, 3, 1, 1, True),  # NDPI version
            (65421, 11, 1, objective, True),  # SourceLens
            (271, 2, None, "Hamamatsu", True),  # Make
            (272, 2, None, "C13220", True),  # Model
        ],
    )
    return buffer.getvalue()


def leica_bytes(xml: str = SCN_XML) -> bytes:
    """A Leica SCN: two tiled levels, the XML on the first page."""
    buffer = io.BytesIO()
    tiled = {
        "photometric": "rgb",
        "metadata": None,
        "tile": (128, 128),
        "compression": "deflate",
    }
    with tifffile.TiffWriter(buffer) as writer:
        writer.write(_rgb(256, 256), description=xml, **tiled)
        writer.write(_rgb(128, 128), description="", **tiled)
    return buffer.getvalue()


def ventana_bytes(xmp: bytes = VENTANA_XMP) -> bytes:
    """A Ventana BIF: tag 700, ``Ventana`` software, and a label page.

    tifffile reads the level order out of the ``level=`` items in each page's
    description and the label out of the literal description ``Label Image``.
    """
    buffer = io.BytesIO()
    tiled = {
        "photometric": "rgb",
        "metadata": None,
        "tile": (128, 128),
        "compression": "deflate",
    }
    with tifffile.TiffWriter(buffer) as writer:
        writer.write(
            _rgb(256, 256),
            description="level=0 mag=40 quality=90",
            software="Ventana Scanner",
            extratags=[(700, 1, len(xmp), xmp, True)],
            **tiled,
        )
        writer.write(_rgb(128, 128), description="level=1 mag=20 quality=90", **tiled)
        writer.write(
            _rgb(32, 32),
            photometric="rgb",
            metadata=None,
            description="Label Image",
        )
    return buffer.getvalue()


def akoya_bytes(description: str = QPI_XML, *, mpp: float = 0.5) -> bytes:
    """An Akoya qptiff: ``PerkinElmer-QPI`` software, base, thumbnail, level.

    The thumbnail sits between the base and the first reduced level, which is
    the order tifffile's QPI series builder walks.
    """
    buffer = io.BytesIO()
    plane = {"photometric": "rgb", "metadata": None, "software": "PerkinElmer-QPI"}
    tiled = {**plane, "tile": (128, 128), "compression": "deflate"}
    with tifffile.TiffWriter(buffer) as writer:
        writer.write(
            _rgb(256, 256),
            description=description,
            resolution=(10000 / mpp, 10000 / mpp),
            resolutionunit="CENTIMETER",
            **tiled,
        )
        writer.write(_rgb(64, 64), description=description, **plane)
        writer.write(_rgb(128, 128), description=description, **tiled)
    return buffer.getvalue()


#: Vendor name -> builder. The names are the ones the reader reports.
WSI_BUILDERS: dict[str, Callable[..., bytes]] = {
    "aperio": aperio_bytes,
    "hamamatsu": hamamatsu_bytes,
    "leica": leica_bytes,
    "ventana": ventana_bytes,
    "akoya": akoya_bytes,
}


def wsi_bytes(vendor: str = "aperio", **kwargs) -> bytes:
    """One synthetic whole-slide image, by the vendor that would have written it."""
    return WSI_BUILDERS[vendor](**kwargs)


#: One Aperio slide, built once so the bytes are the same on every run.
APERIO_SVS = aperio_bytes()


def _wsi() -> list:
    """Aperio is the vendor most public pathology archives publish."""
    return [("slide.svs", APERIO_SVS)]


def _images() -> list:
    """A PNG and a three-channel OME-TIFF: the two collections the handler splits.

    The TIFF is appended rather than prepended, because ``probe_name()`` and the
    exclusive-format sweep both read element 0.
    """
    return [
        ("pixel.png", PNG_1X1),
        ("sample.ome.tif", OME_TIFF),
        ("plain.tif", tiff_bytes()),
    ]


def _dicom() -> list:
    source = next(_SPECT.rglob("*.dcm"), None)
    assert source is not None, f"tracked DICOM fixture missing under {_SPECT}"
    return [("scan.dcm", source.read_bytes())]


def _soft() -> list:
    """A miniature GEO family export: one series, one platform, two samples.

    Small, but not degenerate. The platform and both samples carry inline
    tables, the samples carry characteristics, and ``!Series_sample_id``
    repeats — so the sweep sees the shapes a real deposit has rather than an
    attribute block on its own.
    """
    return [
        (
            "GSE1_family.soft",
            b"^DATABASE = GeoMiame\n"
            b"!Database_name = Gene Expression Omnibus (GEO)\n"
            b"^SERIES = GSE1\n"
            b"!Series_title = A miniature series\n"
            b"!Series_sample_id = GSM1\n"
            b"!Series_sample_id = GSM2\n"
            b"^PLATFORM = GPL1\n"
            b"!Platform_title = A miniature platform\n"
            b"!Platform_data_row_count = 2\n"
            b"#ID = Probe set identifier\n"
            b"!platform_table_begin\n"
            b"ID\tGB_ACC\n"
            b"1_at\tU48705\n"
            b"2_at\tM87338\n"
            b"!platform_table_end\n"
            b"^SAMPLE = GSM1\n"
            b"!Sample_title = First sample\n"
            b"!Sample_characteristics_ch1 = tissue: liver\n"
            b"!Sample_data_row_count = 2\n"
            b"#VALUE = Intensity\n"
            b"!sample_table_begin\n"
            b"ID_REF\tVALUE\n"
            b"1_at\t320.5\n"
            b"2_at\t388.4\n"
            b"!sample_table_end\n"
            b"^SAMPLE = GSM2\n"
            b"!Sample_title = Second sample\n"
            b"!Sample_characteristics_ch1 = tissue: kidney\n"
            b"!Sample_data_row_count = 2\n"
            b"!sample_table_begin\n"
            b"ID_REF\tVALUE\n"
            b"1_at\t305.4\n"
            b"2_at\t339.2\n"
            b"!sample_table_end\n",
        )
    ]


def _hdf5() -> list:
    """A 10x feature matrix: the smallest sample that exercises a layout."""
    from tests.hdf5_fixtures import tenx_bytes

    return [("filtered_feature_bc_matrix.h5", tenx_bytes())]


def _nifti() -> list:
    source = next(_SPECT.rglob("*.nii.gz"), None)
    assert source is not None, f"tracked NIfTI fixture missing under {_SPECT}"
    return [("scan.nii", gzip.decompress(source.read_bytes()))]


#: Handler class name -> builder returning ``[(logical name, plain bytes)]``.
#: A list rather than one pair so a handler whose FileSets span several files
#: can say so: FHIR chunks are the shape that produced the phantom ``.gz.gz``
#: includes. This is the single place a new handler registers test data.
SAMPLES: dict[str, Callable[[], list]] = {
    "CSVHandler": _csv,
    "TSVHandler": _tsv,
    "JSONHandler": _jsonl,
    "FHIRHandler": _ndjson,
    "ParquetHandler": _parquet,
    "ImageHandler": _images,
    "WSIHandler": _wsi,
    "DICOMHandler": _dicom,
    "NIfTIHandler": _nifti,
    "SOFTHandler": _soft,
    "HDF5Handler": _hdf5,
}

#: Handlers with no sample, and why.
EXEMPT: dict[str, str] = {
    "WFDBHandler": (
        "a WFDB record is a header read with its sibling .dat and .atr files, "
        "so no single stream carries it; a compressed .hea is reported instead"
    )
}


def write_wrapped(directory: Path, name: str, payload: bytes, suffix: str = "") -> Path:
    """Write ``payload`` to ``directory/name+suffix``, compressing if asked."""
    target = directory / f"{name}{suffix}"
    if not suffix:
        target.write_bytes(payload)
        return target
    comp = compression.compression_for(target.name)
    assert comp is not None, f"{suffix!r} is not a registered compression"
    with comp.opener(target, "wb") as fh:
        fh.write(payload)
    return target


def write_all(directory: Path, files: Iterable[tuple], suffix: str = "") -> None:
    for name, payload in files:
        write_wrapped(directory, name, payload, suffix)


def bake(directory: Path, **kwargs) -> dict:
    """Bake ``directory`` and return the document."""
    kwargs.setdefault("name", "test")
    return MetadataGenerator(dataset_path=str(directory), **kwargs).generate_metadata()


def bake_with_report(directory: Path, **kwargs) -> tuple[dict, ScanReport]:
    """Bake ``directory`` and return ``(document, scan report)``."""
    kwargs.setdefault("name", "test")
    generator = MetadataGenerator(dataset_path=str(directory), **kwargs)
    return generator.generate_metadata(), generator.scan_report


def bake_with(handlers: Iterable[FileTypeHandler], directory: Path, **kwargs):
    """Bake with ``handlers`` ahead of the built-ins. Returns ``(doc, report)``."""
    return bake_with_report(
        directory, handlers=HandlerRegistry([*handlers, *builtin_handlers()]), **kwargs
    )


runner = CliRunner()


def cli(dataset: Path, output: Path, *extra: str):
    """Invoke the CLI over ``dataset`` with the minimum viable flag set."""
    return runner.invoke(
        app,
        [
            "--input",
            str(dataset),
            "--output",
            str(output),
            "--creator",
            "Tester",
            "--no-validate",
            *extra,
        ],
    )


def _typed(doc: dict, node_type: str) -> list:
    return [n for n in doc.get("distribution", []) if n.get("@type") == node_type]


def file_objects(doc: dict) -> list:
    return _typed(doc, "cr:FileObject")


def file_sets(doc: dict) -> list:
    return _typed(doc, "cr:FileSet")


def record_sets(doc: dict) -> list:
    return doc.get("recordSet", [])


def as_list(value) -> list:
    """mlcroissant collapses a single-element list to a scalar; undo that."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def includes(file_set: dict) -> list:
    return as_list(file_set.get("includes"))


def file_set_members(file_set: dict, directory: Path) -> set[str]:
    """Spec membership using filesystem globs, not mlcroissant's record reader.

    Reader compatibility is checked separately in ``test_ome_filesets.py``;
    mlcroissant 1.1.0 currently ignores exclusions.
    """

    def matched(key):
        return {
            str(path.relative_to(directory))
            for pattern in as_list(file_set.get(key))
            for path in directory.glob(pattern)
            if path.is_file()
        }

    return matched("includes") - matched("cr:excludes")


def by_name(nodes: Iterable[dict], key: str = "name") -> dict:
    return {n[key]: n for n in nodes}


#: The built-in wrapper suffixes tests parametrise over.
WRAPPER_SUFFIXES = [c.suffix for c in compression.BUILTIN_COMPRESSIONS]

__all__ = [
    "DATA",
    "EXEMPT",
    "OME_NAMESPACE",
    "OME_PIXELS",
    "OME_TIFF",
    "PNG_1X1",
    "SAMPLES",
    "APERIO_DESCRIPTION",
    "APERIO_HEADER",
    "APERIO_SVS",
    "QPI_XML",
    "SCN_BOMB",
    "SCN_MALFORMED",
    "SCN_XML",
    "VENTANA_XMP",
    "WRAPPER_SUFFIXES",
    "aperio_bytes",
    "akoya_bytes",
    "hamamatsu_bytes",
    "leica_bytes",
    "ventana_bytes",
    "wsi_bytes",
    "bake",
    "bake_with",
    "bake_with_report",
    "by_name",
    "cli",
    "file_objects",
    "file_sets",
    "file_set_members",
    "includes",
    "ome_bomb",
    "ome_image",
    "ome_xml",
    "record_sets",
    "tiff_bytes",
    "runner",
    "write_all",
    "write_wrapped",
]
