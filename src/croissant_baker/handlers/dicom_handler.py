"""DICOM file handler for medical imaging datasets."""

import logging
from typing import Dict, List, Optional

import mlcroissant as mlc
import pydicom

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".dcm", ".dicom"}

MIME_TYPE = "application/dicom"

_DICOM_MAGIC_OFFSET = 128
_DICOM_MAGIC = b"DICM"


def _has_dicom_magic(source: FileSource) -> bool:
    head = source.peek(_DICOM_MAGIC_OFFSET + 4)
    return head[_DICOM_MAGIC_OFFSET : _DICOM_MAGIC_OFFSET + 4] == _DICOM_MAGIC


def _safe_get(ds, keyword: str, default=None):
    try:
        val = getattr(ds, keyword, None)
        if val is None:
            return default
        if hasattr(val, "__iter__") and not isinstance(val, (str, bytes)):
            return [float(v) for v in val]
        return val
    except Exception:
        return default


def _read_dicom_properties(source: FileSource) -> Dict:
    with source.open() as stream:
        ds = pydicom.dcmread(stream, stop_before_pixels=True)

    props: Dict = {}

    rows = _safe_get(ds, "Rows")
    columns = _safe_get(ds, "Columns")
    if rows is not None:
        props["rows"] = int(rows)
    if columns is not None:
        props["columns"] = int(columns)

    num_frames = _safe_get(ds, "NumberOfFrames")
    props["num_frames"] = int(num_frames) if num_frames is not None else 1

    bits_allocated = _safe_get(ds, "BitsAllocated")
    if bits_allocated is not None:
        props["bits_allocated"] = int(bits_allocated)

    samples_per_pixel = _safe_get(ds, "SamplesPerPixel")
    if samples_per_pixel is not None:
        props["samples_per_pixel"] = int(samples_per_pixel)

    photometric = _safe_get(ds, "PhotometricInterpretation")
    if photometric is not None:
        props["photometric_interpretation"] = str(photometric).strip()

    pixel_spacing = _safe_get(ds, "PixelSpacing")
    if pixel_spacing is not None:
        props["pixel_spacing"] = pixel_spacing

    slice_thickness = _safe_get(ds, "SliceThickness")
    if slice_thickness is not None:
        try:
            props["slice_thickness"] = float(slice_thickness)
        except (ValueError, TypeError):
            pass

    modality = _safe_get(ds, "Modality")
    if modality is not None:
        props["modality"] = str(modality).strip()

    study_desc = _safe_get(ds, "StudyDescription")
    if study_desc is not None:
        props["study_description"] = str(study_desc).strip()

    series_desc = _safe_get(ds, "SeriesDescription")
    if series_desc is not None:
        props["series_description"] = str(series_desc).strip()

    manufacturer = _safe_get(ds, "Manufacturer")
    if manufacturer is not None:
        props["manufacturer"] = str(manufacturer).strip()

    sop_class = _safe_get(ds, "SOPClassUID")
    if sop_class is not None:
        props["sop_class_uid"] = str(sop_class)

    # Hierarchy IDs — needed to regroup files into the
    # patient/study/series tree. PatientID is the root, then StudyInstanceUID
    # for one clinical visit, then SeriesInstanceUID for one acquisition.
    patient_id = _safe_get(ds, "PatientID")
    if patient_id is not None:
        props["patient_id"] = str(patient_id)

    study_uid = _safe_get(ds, "StudyInstanceUID")
    if study_uid is not None:
        props["study_instance_uid"] = str(study_uid)

    series_uid = _safe_get(ds, "SeriesInstanceUID")
    if series_uid is not None:
        props["series_instance_uid"] = str(series_uid)

    return props


class DICOMHandler(FileTypeHandler):
    """
    Handler for DICOM medical imaging files (.dcm, .dicom).

    - Detects files by extension or DICOM magic bytes (DICM at offset 128)
    - Extracts image geometry, pixel encoding, modality, and acquisition
      parameters using pydicom (header only — no pixel data loaded)
    - Computes SHA256 for reproducibility
    """

    EXTENSIONS = (".dcm", ".dicom")
    FORMAT_NAME = "DICOM"
    FORMAT_DESCRIPTION = (
        "Image geometry, modality, pixel encoding, acquisition parameters"
    )

    def claims(self, source: FileSource) -> bool:
        # Files without the DICM preamble are DICOMDIR fragment references, not
        # standalone DICOM. Absent the file, this is a path-only lookup and the
        # extension is all there is to go on.
        if source.suffix in SUPPORTED_EXTENSIONS:
            return _has_dicom_magic(source) if source.exists else True
        if not source.suffix and source.exists:
            return _has_dicom_magic(source)
        return False

    def extract(self, source: FileSource, **kwargs) -> dict:
        if not source.exists:
            raise FileNotFoundError(f"DICOM file not found: {source.relative_path}")

        try:
            props = _read_dicom_properties(source)
        except Exception as e:
            raise ValueError(
                f"Failed to read DICOM file {source.relative_path}: {e}"
            ) from e

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": MIME_TYPE,
            "dicom_properties": props,
        }

    def build_croissant(
        self, file_metas: list[dict], file_ids: list[str]
    ) -> tuple[list, list]:
        # An empty batch has nothing to summarise; emitting a FileSet over
        # zero files would describe data that is not there.
        if not file_metas:
            return BuildResult([], [])

        summary = collect_dicom_summary(file_metas)

        num_files = summary.get("num_files", len(file_metas))
        modality_counts = summary.get("modality_counts", {})
        modalities_str = (
            ", ".join(f"{m} ({c})" for m, c in modality_counts.items())
            if modality_counts
            else "unknown modality"
        )

        rows_range = summary.get("rows_range")
        cols_range = summary.get("columns_range")
        if rows_range and cols_range:
            if rows_range[0] == rows_range[1] and cols_range[0] == cols_range[1]:
                dims_note = f"{rows_range[0]}x{cols_range[0]}"
            else:
                dims_note = (
                    f"{rows_range[0]}-{rows_range[1]}x{cols_range[0]}-{cols_range[1]}"
                )
        else:
            dims_note = "unknown dimensions"

        fileset_id = "dicom-files"
        dicom_fileset = mlc.FileSet(
            id=fileset_id,
            name="DICOM files",
            description=f"{num_files} DICOM file(s) ({modalities_str})",
            encoding_formats=[MIME_TYPE],
            includes=["**/*.dcm", "**/*.dicom"],
        )

        fields = [
            mlc.Field(
                id="dicom/modality",
                name="modality",
                description="DICOM Modality (0008,0060)",
                data_types=["sc:Text"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
            mlc.Field(
                id="dicom/rows",
                name="rows",
                description="DICOM Rows (0028,0010)",
                data_types=["sc:Integer"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
            mlc.Field(
                id="dicom/columns",
                name="columns",
                description="DICOM Columns (0028,0011)",
                data_types=["sc:Integer"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
            mlc.Field(
                id="dicom/num_frames",
                name="num_frames",
                description="DICOM NumberOfFrames (0028,0008); >1 for multi-frame / cine DICOM",
                data_types=["sc:Integer"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
            mlc.Field(
                id="dicom/bits_allocated",
                name="bits_allocated",
                description="DICOM BitsAllocated (0028,0100); bits per pixel sample",
                data_types=["sc:Integer"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
            mlc.Field(
                id="dicom/patient_id",
                name="patient_id",
                description="DICOM PatientID (0010,0020); root of the patient/study/series/instance hierarchy",
                data_types=["sc:Text"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
            mlc.Field(
                id="dicom/study_instance_uid",
                name="study_instance_uid",
                description="DICOM StudyInstanceUID (0020,000D); UID grouping all series from one patient visit",
                data_types=["sc:Text"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
            mlc.Field(
                id="dicom/series_instance_uid",
                name="series_instance_uid",
                description="DICOM SeriesInstanceUID (0020,000E); UID grouping slices from one acquisition",
                data_types=["sc:Text"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
        ]

        dicom_record_set = mlc.RecordSet(
            id="dicom",
            name="dicom",
            description=f"{num_files} DICOM files ({dims_note}): {modalities_str}",
            fields=fields,
        )

        return BuildResult([dicom_fileset], [dicom_record_set])


def collect_dicom_summary(dicom_metadata_list: List[Dict]) -> Dict:
    if not dicom_metadata_list:
        return {}

    rows_list: List[int] = []
    cols_list: List[int] = []
    frames_list: List[int] = []
    modalities: Dict[str, int] = {}
    bits_set: set = set()
    unknown_modality = 0

    for meta in dicom_metadata_list:
        props = meta.get("dicom_properties", {})

        if "rows" in props:
            rows_list.append(props["rows"])
        if "columns" in props:
            cols_list.append(props["columns"])
        if "num_frames" in props:
            frames_list.append(props["num_frames"])
        if "bits_allocated" in props:
            bits_set.add(props["bits_allocated"])

        modality: Optional[str] = props.get("modality")
        if modality:
            modalities[modality] = modalities.get(modality, 0) + 1
        else:
            # Tag (0008,0060) is type 1 in many SOP classes but optional in
            # others; PhysioNet test files include real DICOMs with no
            # modality. Surface them as "unknown" so the per-modality counts
            # add up to num_files.
            unknown_modality += 1
    if unknown_modality:
        modalities["unknown"] = unknown_modality

    summary: Dict = {"num_files": len(dicom_metadata_list)}

    if rows_list:
        summary["rows_range"] = (min(rows_list), max(rows_list))
    if cols_list:
        summary["columns_range"] = (min(cols_list), max(cols_list))
    if frames_list:
        summary["frames_range"] = (min(frames_list), max(frames_list))
    if modalities:
        summary["modality_counts"] = modalities
    if bits_set:
        summary["bits_allocated_values"] = sorted(bits_set)

    return summary
