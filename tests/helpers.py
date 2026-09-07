"""Shared test vocabulary: sample data, fixture writing, baking, navigation."""

from __future__ import annotations

import base64
import gzip
import io
from pathlib import Path
from typing import Callable, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

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


def _png() -> list:
    return [("pixel.png", PNG_1X1)]


def _dicom() -> list:
    source = next(_SPECT.rglob("*.dcm"), None)
    assert source is not None, f"tracked DICOM fixture missing under {_SPECT}"
    return [("scan.dcm", source.read_bytes())]


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
    "ImageHandler": _png,
    "DICOMHandler": _dicom,
    "NIfTIHandler": _nifti,
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


def by_name(nodes: Iterable[dict], key: str = "name") -> dict:
    return {n[key]: n for n in nodes}


#: The built-in wrapper suffixes tests parametrise over.
WRAPPER_SUFFIXES = [c.suffix for c in compression.BUILTIN_COMPRESSIONS]

__all__ = [
    "DATA",
    "EXEMPT",
    "PNG_1X1",
    "SAMPLES",
    "WRAPPER_SUFFIXES",
    "bake",
    "bake_with",
    "bake_with_report",
    "by_name",
    "cli",
    "file_objects",
    "file_sets",
    "includes",
    "record_sets",
    "runner",
    "write_all",
    "write_wrapped",
]
