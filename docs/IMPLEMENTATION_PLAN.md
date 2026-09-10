# Implementation plan: digital pathology whole-slide image support

Closes MIT-LCP/croissant-baker#137.

Scope decision: the formats that cover the bulk of public pathology datasets
(TCGA, CPTAC, CAMELYON, and most vendor exports) are TIFF containers that the
already-required `tifffile` library opens. This PR adds those with no new
dependency, mirroring how BigTIFF and OME-TIFF were added. DICOM WSI is added
on top of the existing DICOM handler. Formats that need a new library (CZI,
VSI, iSyntax) or a directory-as-one-unit discovery concept that the codebase
does not have (MIRAX, OME-Zarr, DZI) are documented as not yet supported.

## Stage 1: Vendor TIFF whole-slide handler
**Goal**: A `WSIHandler` claiming `.svs`, `.ndpi`, `.scn`, `.bif`, `.qptiff`
by extension plus TIFF magic, backed by a pure `handlers/wsi.py` reader that
knows nothing about Croissant. Extracts vendor, base dimensions, pyramid level
count and per-level dimensions, tile size, microns per pixel, objective
magnification, compression, and associated image kinds (label, macro,
thumbnail), each left `None` when the file does not state it.
**Success Criteria**: Handler passes the shared contract sweep; every vendor
gets its own synthetic fixture; `select_handler` routes each extension to it;
a bake produces a FileSet plus a `slides` RecordSet validated by mlcroissant.
**Tests**: `tests/test_wsi.py` (reader), `tests/test_wsi_handler.py`
(handler), `SAMPLES` entry in `tests/helpers.py`.
**Status**: Complete

## Stage 2: DICOM whole-slide microscopy awareness
**Goal**: The DICOM handler recognises the VL Whole Slide Microscopy Image SOP
class and additionally records image flavor (VOLUME, LABEL, OVERVIEW,
THUMBNAIL), total pixel matrix size, imaged volume size, and pixel spacing
from the shared functional groups. Non-WSI DICOM output is unchanged.
**Success Criteria**: Existing DICOM tests and goldens unchanged; a synthetic
WSI instance yields the new fields; the record-set description names the
slide count.
**Tests**: additions to `tests/test_dicom_handler.py`.
**Status**: Not Started

## Stage 3: Fixtures, end-to-end bake, and documentation
**Goal**: A small committed `tests/data/input/wsi_demo/` dataset with one
synthetic slide per vendor, an end-to-end test validating the bake with
mlcroissant, README table row, `supported-formats.md` section (and removal
of the line saying vendor TIFF is not read), regenerated formats table.
**Success Criteria**: `uv run pytest` green, `docs/generate.py` output
committed, pre-commit clean.
**Tests**: `tests/test_end_to_end.py` addition.
**Status**: Not Started
