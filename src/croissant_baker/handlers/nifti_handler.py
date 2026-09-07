"""NIfTI file handler for neuroimaging datasets."""

import logging
from typing import Dict, List, Optional

import mlcroissant as mlc
import nibabel as nib

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

MIME_TYPE = "application/x-nifti"

#: sizeof_hdr distinguishes the two NIfTI versions.
_NIFTI1_HDR_SIZE = 348
_NIFTI2_HDR_SIZE = 540


def _image_class(header: bytes):
    """Pick the nibabel image class from the first four header bytes.

    ``sizeof_hdr`` is 348 for NIfTI-1 and 540 for NIfTI-2, in the file's own
    endianness.
    """
    size = int.from_bytes(header, "little")
    if size not in (_NIFTI1_HDR_SIZE, _NIFTI2_HDR_SIZE):
        size = int.from_bytes(header, "big")
    return nib.Nifti2Image if size == _NIFTI2_HDR_SIZE else nib.Nifti1Image


def _read_nifti_properties(source: FileSource) -> Dict:
    """
    Read NIfTI metadata from a stream (header only, data array never loaded).

    Extracts spatial dimensions, voxel spacing, data type, and TR for 4D volumes.
    Works for both NIfTI-1 and NIfTI-2. Read from the stream rather than by
    path, because ``nib.load`` carries an opener table of its own.
    """
    with source.open() as stream:
        klass = _image_class(stream.read(4))
        stream.seek(0)
        holder = nib.FileHolder(fileobj=stream)
        img = klass.from_file_map({"header": holder, "image": holder})
        hdr = img.header

        props: Dict = {}

        # NIfTI-1 and NIfTI-2 both expose get_data_shape() and get_zooms().
        shape = img.shape
        ndim = len(shape)

        if ndim >= 1:
            props["dim_x"] = int(shape[0])
        if ndim >= 2:
            props["dim_y"] = int(shape[1])
        if ndim >= 3:
            props["dim_z"] = int(shape[2])
        if ndim >= 4:
            props["dim_t"] = int(shape[3])

        props["ndim"] = ndim

        # Voxel spacing in mm (pixdim[1:4] for spatial dims).
        zooms = hdr.get_zooms()
        if len(zooms) >= 1:
            props["voxel_spacing_x"] = float(zooms[0])
        if len(zooms) >= 2:
            props["voxel_spacing_y"] = float(zooms[1])
        if len(zooms) >= 3:
            props["voxel_spacing_z"] = float(zooms[2])

        # TR (repetition time in seconds) — only meaningful for 4D fMRI data.
        if ndim >= 4 and len(zooms) >= 4:
            tr = float(zooms[3])
            if tr > 0:
                props["tr_seconds"] = tr

        # Data type stored in the file.
        try:
            props["data_dtype"] = str(hdr.get_data_dtype())
        except Exception:
            pass

        props["nifti_version"] = 2 if klass is nib.Nifti2Image else 1

        return props


class NIfTIHandler(FileTypeHandler):
    """
    Handler for NIfTI neuroimaging files (.nii).

    - Detects files by extension (.nii)
    - Extracts spatial dimensions, voxel spacing, data type, TR (for fMRI),
      and NIfTI version using nibabel (header only — data array never loaded)
    - Computes SHA256 for reproducibility
    """

    EXTENSIONS = (".nii",)
    FORMAT_NAME = "NIfTI"
    FORMAT_DESCRIPTION = (
        "Spatial dimensions, voxel spacing, data type, TR for fMRI volumes"
    )

    def claims(self, source: FileSource) -> bool:
        return source.suffix == ".nii"

    def extract(self, source: FileSource, **kwargs) -> dict:
        if not source.exists:
            raise FileNotFoundError(f"NIfTI file not found: {source.relative_path}")

        try:
            props = _read_nifti_properties(source)
        except Exception as e:
            raise ValueError(
                f"Failed to read NIfTI file {source.relative_path}: {e}"
            ) from e

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": MIME_TYPE,
            "nifti_properties": props,
        }

    def build_croissant(
        self, file_metas: list[dict], file_ids: list[str]
    ) -> tuple[list, list]:
        # An empty batch has nothing to summarise; emitting a FileSet over
        # zero files would describe data that is not there.
        if not file_metas:
            return BuildResult([], [])

        summary = collect_nifti_summary(file_metas)

        num_files = summary.get("num_files", len(file_metas))

        # Spatial dims description.
        dims_parts = []
        for key, label in [
            ("dim_x_range", "x"),
            ("dim_y_range", "y"),
            ("dim_z_range", "z"),
        ]:
            r = summary.get(key)
            if r:
                dims_parts.append(f"{r[0]}" if r[0] == r[1] else f"{r[0]}-{r[1]}")
        dims_note = "x".join(dims_parts) if dims_parts else "unknown dims"

        # 4D note. Collapse to a single number when min == max so a
        # homogeneous 4D set reads "100 volumes" rather than "100-100 volumes",
        # matching the spatial-dim formatter above.
        t_range = summary.get("dim_t_range")
        ndim_max = summary.get("ndim_max", 3)
        if ndim_max >= 4 and t_range:
            if t_range[0] == t_range[1]:
                dims_note += f", {t_range[0]} volumes"
            else:
                dims_note += f", {t_range[0]}-{t_range[1]} volumes"

        dtype_counts = summary.get("dtype_counts", {})
        dtypes_str = ", ".join(dtype_counts.keys()) if dtype_counts else "unknown dtype"

        mime_types = {meta["encoding_format"] for meta in file_metas}
        includes = ["**/*.nii"]

        fileset_id = "nifti-files"
        nifti_fileset = mlc.FileSet(
            id=fileset_id,
            name="NIfTI files",
            description=f"{num_files} NIfTI file(s) ({dims_note})",
            encoding_formats=sorted(mime_types),
            includes=includes,
        )

        fields = [
            mlc.Field(
                id="nifti/dim_x",
                name="dim_x",
                description="NIfTI dim[1]; voxel grid size along x axis",
                data_types=["sc:Integer"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
            mlc.Field(
                id="nifti/dim_y",
                name="dim_y",
                description="NIfTI dim[2]; voxel grid size along y axis",
                data_types=["sc:Integer"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
            mlc.Field(
                id="nifti/dim_z",
                name="dim_z",
                description="NIfTI dim[3]; voxel grid size along z axis",
                data_types=["sc:Integer"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
            mlc.Field(
                id="nifti/voxel_spacing",
                name="voxel_spacing",
                description="NIfTI pixdim[1:4]; voxel size in mm along x, y, z",
                data_types=["sc:Text"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
            mlc.Field(
                id="nifti/data_dtype",
                name="data_dtype",
                description=f"NIfTI datatype; stored data type ({dtypes_str})",
                data_types=["sc:Text"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
            mlc.Field(
                id="nifti/nifti_version",
                name="nifti_version",
                description="Inferred from sizeof_hdr (348=NIfTI-1, 540=NIfTI-2)",
                data_types=["sc:Integer"],
                source=mlc.Source(
                    file_set=fileset_id,
                    extract=mlc.Extract(file_property="content"),
                ),
            ),
        ]

        # Only add TR field if any file in this dataset is 4D.
        if ndim_max >= 4:
            fields.append(
                mlc.Field(
                    id="nifti/tr_seconds",
                    name="tr_seconds",
                    description="NIfTI pixdim[4]; repetition time in seconds for fMRI volumes",
                    data_types=["sc:Float"],
                    source=mlc.Source(
                        file_set=fileset_id,
                        extract=mlc.Extract(file_property="content"),
                    ),
                )
            )

        nifti_record_set = mlc.RecordSet(
            id="nifti",
            name="nifti",
            description=f"{num_files} NIfTI files ({dims_note}): {dtypes_str}",
            fields=fields,
        )

        return BuildResult([nifti_fileset], [nifti_record_set])


def collect_nifti_summary(nifti_metadata_list: List[Dict]) -> Dict:
    if not nifti_metadata_list:
        return {}

    dx, dy, dz, dt = [], [], [], []
    ndim_max = 0
    dtype_counts: Dict[str, int] = {}
    tr_list = []

    for meta in nifti_metadata_list:
        props = meta.get("nifti_properties", {})

        if "dim_x" in props:
            dx.append(props["dim_x"])
        if "dim_y" in props:
            dy.append(props["dim_y"])
        if "dim_z" in props:
            dz.append(props["dim_z"])
        if "dim_t" in props:
            dt.append(props["dim_t"])

        ndim_max = max(ndim_max, props.get("ndim", 0))

        dtype: Optional[str] = props.get("data_dtype")
        if dtype:
            dtype_counts[dtype] = dtype_counts.get(dtype, 0) + 1

        tr = props.get("tr_seconds")
        if tr is not None:
            tr_list.append(tr)

    summary: Dict = {"num_files": len(nifti_metadata_list), "ndim_max": ndim_max}

    if dx:
        summary["dim_x_range"] = (min(dx), max(dx))
    if dy:
        summary["dim_y_range"] = (min(dy), max(dy))
    if dz:
        summary["dim_z_range"] = (min(dz), max(dz))
    if dt:
        summary["dim_t_range"] = (min(dt), max(dt))
    if dtype_counts:
        summary["dtype_counts"] = dtype_counts
    if tr_list:
        summary["tr_range"] = (min(tr_list), max(tr_list))

    return summary
