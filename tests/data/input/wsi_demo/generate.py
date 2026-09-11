#!/usr/bin/env python3
"""Write the whole-slide demo fixture: one slide per vendor, and a DICOM slide.

Run from the repository root:

    uv run --no-sync python tests/data/input/wsi_demo/generate.py

The five vendor TIFFs come from the builders the unit tests already use, so
the fixture and the unit tests describe the same synthetic scanners. The DICOM
instance is built here because its UIDs are fixed: a committed fixture whose
identifiers changed on every run would rewrite the golden document with it.

Every file is zero-pixel and carries no patient data. See README.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
sys.path.insert(0, str(REPO_ROOT))

from tests.helpers import WSI_BUILDERS  # noqa: E402

#: Vendor -> the file name a scanner of that make would have written.
SLIDES = {
    "aperio": "aperio.svs",
    "hamamatsu": "hamamatsu.ndpi",
    "leica": "leica.scn",
    "ventana": "ventana.bif",
    "akoya": "akoya.qptiff",
}

#: VL Whole Slide Microscopy Image Storage.
WSI_SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.77.1.6"

#: Fixed rather than generated, so regenerating the fixture leaves the working
#: tree clean. The prefix is pydicom's own, and the suffixes are synthetic.
STUDY_UID = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000001"
SERIES_UID = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000002"
INSTANCE_UID = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000003"
LABEL_SERIES_UID = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000004"
LABEL_INSTANCE_UID = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000005"

#: The barcode both instances carry: one piece of glass, imaged twice.
CONTAINER_ID = "SLIDE-0001"


def write_dicom_slide(
    path: Path,
    *,
    series_uid: str = SERIES_UID,
    instance_uid: str = INSTANCE_UID,
    flavor: str = "VOLUME",
    size: int = 512,
    frames: int = 12,
    total_columns: int = 4096,
    total_rows: int = 2048,
    imaged_volume: tuple = (24.5, 16.4),
    pixel_spacing: tuple = (0.00025, 0.00025),
) -> None:
    """One minimal whole-slide microscopy instance, with no frames.

    A real slide is a tiled multi-frame pyramid of gigabytes. Everything the
    handler reads lives in the header, so this one carries the header tags and
    no pixel data at all.
    """
    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = WSI_SOP_CLASS_UID
    file_meta.MediaStorageSOPInstanceUID = instance_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\x00" * 128)

    ds.SOPClassUID = WSI_SOP_CLASS_UID
    ds.SOPInstanceUID = instance_uid
    ds.StudyInstanceUID = STUDY_UID
    ds.SeriesInstanceUID = series_uid
    ds.Modality = "SM"
    ds.ImageType = ["ORIGINAL", "PRIMARY", flavor, "NONE"]
    ds.Rows = size
    ds.Columns = size
    ds.NumberOfFrames = frames
    ds.BitsAllocated = 8
    ds.SamplesPerPixel = 3
    ds.PhotometricInterpretation = "YBR_FULL_422"
    ds.Manufacturer = "Synthetic Scanner"
    ds.TotalPixelMatrixColumns = total_columns
    ds.TotalPixelMatrixRows = total_rows
    ds.ContainerIdentifier = CONTAINER_ID
    if imaged_volume is not None:
        ds.ImagedVolumeWidth, ds.ImagedVolumeHeight = imaged_volume

    # Required of every whole slide instance, the label included, and read by
    # nothing here: a slide states its illumination and filters in it.
    ds.OpticalPathSequence = [Dataset()]

    if pixel_spacing is not None:
        # A slide states its physical scale one nesting down, in the shared
        # functional groups, rather than in the top-level PixelSpacing a
        # single frame image uses.
        measures = Dataset()
        measures.PixelSpacing = list(pixel_spacing)
        shared = Dataset()
        shared.PixelMeasuresSequence = [measures]
        ds.SharedFunctionalGroupsSequence = [shared]

    path.parent.mkdir(parents=True, exist_ok=True)
    pydicom.dcmwrite(str(path), ds)


def main() -> None:
    for vendor, name in SLIDES.items():
        target = HERE / name
        target.write_bytes(WSI_BUILDERS[vendor]())
        print(f"wrote {target.relative_to(REPO_ROOT)} ({target.stat().st_size} bytes)")

    slide = HERE / "dicom" / "slide.dcm"
    write_dicom_slide(slide)
    print(f"wrote {slide.relative_to(REPO_ROOT)} ({slide.stat().st_size} bytes)")

    # The barcode label of the same piece of glass, which a scanner files as
    # its own instance. A second flavor is what makes the flavor list in the
    # DICOM description more than one word long.
    label = HERE / "dicom" / "label.dcm"
    write_dicom_slide(
        label,
        series_uid=LABEL_SERIES_UID,
        instance_uid=LABEL_INSTANCE_UID,
        flavor="LABEL",
        size=64,
        frames=1,
        total_columns=64,
        total_rows=64,
        # A LABEL instance routinely states neither: it is a photograph of the
        # barcode rather than a sampling of tissue.
        imaged_volume=None,
        pixel_spacing=None,
    )
    print(f"wrote {label.relative_to(REPO_ROOT)} ({label.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
