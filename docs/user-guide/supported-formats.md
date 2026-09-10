# Supported Formats

croissant-baker detects file types automatically. Handlers are checked in the order listed below — the first match wins.

Nothing is skipped in silence, and nothing is reported one line per file by default. The closing coverage summary counts every file the document does not carry, under the reason it was passed over. The summary is a fixed size — a header plus at most one line per reason — so a directory with one undescribed file and one with ten thousand print the same shape. The full list is under `--verbose`, or in the machine-readable `--report` file:

```text
Scanned 8 file(s): 4 described, 1 linked, 1 referenced, 2 not described.
  no handler: 1
  archive, not opened: 1
```

A file reaches the document three ways, and the header names each one that
happened. `described` means a handler read the file and built its record set.
`linked` means the file is another described file in a different form — a
`.csv.gz` beside its `.csv` — so its bytes are described and its structure is
its twin's. `referenced` means another file's handler put it there: a WFDB
header is read together with its `.dat` and `.atr`, and each gets a FileObject
though nothing claims a `.dat` on its own. Only `not described` counts files the
document does not carry, and the reason lines account for exactly those.

`--report FILE` writes one JSON object per run. `outcome` and `reason` come from
fixed vocabularies, so a tool can branch on them without parsing English:

```json
{
  "total": 8,
  "described": 4,
  "linked": 1,
  "referenced": 1,
  "undescribed": 2,
  "by_reason": {"no_handler": 1, "archive": 1},
  "files": [
    {"path": "notes.txt", "outcome": "unclaimed", "reason": "no_handler",
     "detail": "no handler for this file type"},
    {"path": "sample.csv.gz", "outcome": "linked", "reason": "duplicate_by_name",
     "detail": "same logical name as sample.csv; linked by naming convention, content not verified",
     "duplicate_of": "sample.csv"},
    {"path": "100.dat", "outcome": "referenced",
     "detail": "described as part of 100.hea", "part_of": "100.hea"}
  ]
}
```

`described`, `linked`, `referenced` and `undescribed` sum to `total`, so a tool
can check coverage without reading the file list. `by_reason` accounts for the
`undescribed` alone.

`outcome` is one of `described`, `linked`, `referenced`, `unclaimed`, `failed`,
or `would_process` (`--dry-run` only). `reason` is one of `no_handler`,
`archive`, `unsupported_input`, `claim_failed`, `extract_failed`,
`build_failed`, `partition_schema_conflict`, `duplicate_by_name`, or
`probable_duplicate`, and is absent on `described` and `referenced`.

## File types

--8<-- "_generated/formats-table.md"

## Compression

`.gz`, `.bz2` and `.xz` are transport, not format. They are resolved before any handler is consulted, so every format in the table above is described the same way whether or not it arrived wrapped: `cells.parquet.gz` produces the record set that `cells.parquet` produces.

What the wrapper changes is the distribution entry, which addresses the bytes on disk. `encodingFormat` carries both media types, and `contentUrl`, `contentSize` and `sha256` are of the file as stored:

```json
{
  "@type": "cr:FileObject",
  "name": "cells.parquet.gz",
  "contentUrl": "cells.parquet.gz",
  "encodingFormat": ["application/vnd.apache.parquet", "application/gzip"]
}
```

Record set identifiers are derived from the logical file — `cells.parquet` — so a compressed file and its plain twin describe one table rather than two. Descriptions name the file as stored (`Records from cells.parquet.gz`), because a description has to name something you can find on disk. FileSet `includes` are resolved against the files actually present, so a compressed file is covered by the FileSet describing it.

WFDB is the exception. A WFDB record is a `.hea` header read together with its sibling `.dat` and `.atr` files, so no single stream carries it; a compressed `.hea` is reported with that as its reason rather than described.

`.zip`, `.tar` and `.tgz` hold several members. They are reported as archives and not opened.

Registering another compression is a library call and needs no handler change:

```python
import zstandard
from croissant_baker import compression

compression.register_compression(
    compression.Compression("zstd", ".zst", "application/zstd", zstandard.open)
)
```

## Duplicate files

Two files in one directory can describe the same data, which would otherwise put two record sets under one identifier and abort the bake. One is described and the others link to it with `sameAs`, keeping their own distribution entries — their bytes, sizes and checksums are their own.

| Shape | How it is decided |
|-------|-------------------|
| `sample.csv` and `sample.csv.gz` | Linked on the naming convention. Contents are not compared, and the reason says so |
| `sample.csv.gz` and `sample.csv.xz` | Linked only if the first 64 KiB decompress identically |
| `sample.csv` and `sample.tsv` | Two plain files of different size are rejected outright; otherwise the same 64 KiB comparison. If they differ, both are described under distinct identifiers (`sample_csv`, `sample_tsv`) |

The file that keeps its structure is chosen deterministically: uncompressed first, then the order compressions were registered in, then the path.

## CSV and TSV

CSV and TSV files are read with PyArrow's streaming reader — memory is constant regardless of file size. Type inference runs in two passes: an initial sweep, then per-column promotion when the first pass hits a type conflict.

Row counts are omitted by default for speed. Pass `--count-csv-rows` to do a full scan for exact counts (slow on large datasets).

## FHIR (`.ndjson`, `.json` Bundle)

Two FHIR serialization formats are supported:

- **NDJSON bulk export** (`.ndjson`): one resource per line, all of the same `resourceType`. Produced by FHIR Bulk Data servers.
- **JSON Bundle** (`.json`): a FHIR Bundle whose `entry[]` may contain mixed resource types.

Field names and types are inferred from a sample of resources. `OperationOutcome` resources (error markers) are skipped.

!!! note
    FHIR `.json` files are detected by content — the handler looks for `"resourceType": "<UpperCase…"` before accepting. Plain JSON files that happen to use `.json` are handled by the JSON handler instead.

## JSON and JSONL

- **JSON** (`.json`): an array of objects (one object per record) or a single object (treated as one record).
- **JSONL** (`.jsonl`): newline-delimited JSON, one object per line.

Schema is inferred from a sample of records. FHIR `.json` files are excluded — they go to the FHIR handler.

## Parquet

Schema is read from Parquet metadata only — the file data is never loaded.

Spark and Arrow write one table as a directory of `part-*.parquet`, while a vendor export directory holds several unrelated tables. Directory membership cannot tell those apart, so two files are shards of one logical table — one `cr:FileSet` and one `cr:RecordSet` — only when both hold:

| Evidence | What counts |
|----------|-------------|
| A shard-shaped name | The names match once digit runs are masked, and at least one of those runs is a name component of its own: the whole stem (`0.parquet`) or introduced by `-`, `_` or `.` (`part-00000.parquet`). Digits fused to letters are part of a word, so `assay1.parquet` and `assay2.parquet` stay two tables |
| An identical Arrow schema | Compared as Arrow types, not as Croissant types, which collapse timestamp units, nullability, decimal precision and nested structure |

Anything else is described on its own, as CSV and JSON files are. Files at the dataset root never pair.

Where files agree on the name but not the schema, that is drift inside one table: the majority schema is described and the rest are reported with reason `partition_schema_conflict` rather than folded in. The `cr:FileSet` then lists its shards individually — a directory-wide glob would re-admit exactly the files that were kept out.

## WFDB

WFDB (WaveForm DataBase) is the standard physiological signal format on PhysioNet. The handler reads the `.hea` header file and records signal channel names, sampling frequency, number of samples, and duration. Associated `.dat` binary files are listed as related files.

Because a record spans several files located by path, this is the one handler that needs a real file on disk. A compressed `.hea` is reported rather than described.

## Images

Standard images (`.png`, `.jpg`, `.gif`, `.bmp`, `.webp`, `.ico`) are read with Pillow. TIFF and BigTIFF files (`.tif`, `.tiff`, `.btf`) are read with `tifffile`, which supports scientific TIFF layouts such as multiband rasters and reads the UTF-8 OME metadata used by microscopy writers. Both TIFF variants use `image/tiff`.

Ordinary images belong to the `image-files` FileSet and `images` RecordSet. Their descriptions summarize dimensions, band counts and formats. Include patterns cover both root-level files (`*.tif`, for example) and nested files (`**/*.tif`), including readers that require a directory for `**/`. Pixel arrays are not decoded during metadata extraction.

!!! note
    Wrapped TIFFs require additional decompression because TIFF readers seek within the file. A backward seek on a compressed stream can restart decompression from the beginning, so reading a `.tif.gz`, `.tif.bz2` or `.tif.xz` can decompress the pixel payload even though no pixel array is decoded. Uncompressed TIFFs can seek directly to their headers.

### OME-TIFF

An OME-TIFF carries OME-XML in its ImageDescription tag. The handler checks the content identified by `tifffile` and requires a versioned OME namespace; the filename's `.ome.` infix does not determine the format. Files with a readable OME header have their own collection:

| Node | Content |
|------|---------|
| `cr:FileSet` `ome-image-files` | the OME files, listed individually |
| `cr:RecordSet` `ome_images` | one row per OME file, with the fields below |

The header fields are `ome_version`, `ome_image_count`, `size_c`, `size_z`, `size_t`, `dimension_order`, `pixel_type`, `physical_size_x`, `physical_size_y`, `physical_size_x_unit`, `physical_size_y_unit`, and the array field `channel_names`. Fields with no observed values are omitted. Invalid dimension counts and physical sizes are omitted independently; physical sizes must be finite and positive.

**Physical measurements retain their original unit metadata.** X and Y have separate unit fields. An absent unit remains `None` in the parsed header, including for older OME schemas; schema defaults are not applied. Explicit unit strings are preserved and measurements are not converted. Batch ranges are grouped by unit, so `0.001 mm` and `0.2–0.4 µm` remain separate summaries. Measurements without a unit are reported separately as `unit unspecified`. A unit field is omitted when no file in the batch declares a nonempty unit for that axis.

**The manifest separates OME files from ordinary images.** The ordinary-image FileSet retains extension globs and excludes the OME paths using `cr:excludes`. This keeps its size proportional to the number of OME exceptions rather than the number of ordinary TIFF tiles. The generator resolves both includes and excludes to stored paths, including compression wrappers and linked duplicates. Collection counts and summaries describe this partition.

!!! note "Reader support for exclusions"
    `mlcroissant` 1.1.0 serializes and validates `cr:excludes`, but its record reader does not apply those exclusions ([upstream issue #772](https://github.com/mlcommons/croissant/issues/772)). In a mixed dataset, `records("images")` can therefore return OME files as well as ordinary images, exceeding the count stated in the description. The same OME files also belong to `ome-image-files`. Consumers must honor includes minus excludes to obtain the intended partition. A regression test reads actual records to track this limitation; successful metadata validation alone does not establish correct record reading.

**Rows describe files.** One OME document may declare several `<Image>` elements, and an image may span several TIFF files. The handler does not group these files or resolve `TiffData` mappings. The Pixels fields describe the first `<Image>` in each file's XML document, which need not correspond to all pixels stored in that particular file. `ome_image_count` counts the document's Image elements. TIFF `SamplesPerPixel` supplies `num_bands`; it can be 1 for an OME image whose three channels occupy separate TIFF pages.

**Header fields are descriptive.** Their descriptions contain observed ranges or sets, and no `Field.value` is emitted. Only `image` has `extract: {fileProperty: content}`. Header fields retain their FileSet source without an extraction instruction, because reading the file content does not select an OME header attribute. `mlcroissant` 1.1.0 cannot extract these fields, so reading the complete `ome_images` RecordSet with `records("ome_images")` fails even when the metadata validates. Channel labels come from `Channel/@Name`; the list omits unnamed channels and is not a positional channel mapping. `Image/@Name`, `Creator`, and file UUIDs are not copied into field descriptions.

For descriptions identified as OME-XML, the parser refuses DTD/entity declarations, malformed XML and descriptions larger than 8 MiB. Declaration-like text inside comments, processing instructions or CDATA is allowed. Such text does not declare a DTD or expand entities. Refused files remain in the ordinary `images` collection with their TIFF properties. The refusal count and reasons appear in that RecordSet's description, and a warning naming the file is available to applications that configure logging. The size limit bounds XML parsing; `tifffile` may already have decoded the TIFF description tag before the check.

A `BinaryOnly` document is a placeholder pointing to a companion metadata file. It remains in `ome_images`, names the companion in its description, and contributes no header measurements or image count. The companion is not opened.

Not read: `Plane`, `Objective`, `TimeIncrement`, plate and well metadata, and pyramid levels. Vendor whole-slide TIFFs (`.svs`, `.ndpi`, `.scn`, `.bif`, `.qptiff`) have a handler of their own, described in the next section.

## Whole-slide images

Digital pathology scanners write their slides into a TIFF container, one private shape per vendor. Five extensions are claimed: `.svs` (Aperio), `.ndpi` (Hamamatsu), `.scn` (Leica), `.bif` (Ventana) and `.qptiff` (Akoya). Both TIFF versions and both byte orders are accepted, because a slide crosses the 4 GiB that sends a writer to BigTIFF more often than not.

The claim is the vendor extension over TIFF magic, never the magic alone. The bytes of a pyramidal TIFF say nothing about whether the pyramid holds tissue, so `.tif` and `.tiff` stay with the image handler even when they are pyramidal. `tifffile` reads the header; no pixel is decoded and no tile is stitched.

What is read from the file: the vendor signature, the base level's width and height, the number of pyramid levels and each level's dimensions, the base level's tile size and compression, microns per pixel across and down, the objective magnification the slide was scanned through, and the kinds of associated image the file carries (`label`, `macro`, `overview`, `thumbnail`). Each is left unstated where the file does not state it: a Leica SCN document puts the imaged area in its `view` element and the level sizes in `pixels`, and dividing one by the other is an inference the file does not make, so a Leica slide reports no microns per pixel.

| Node | Content |
|------|---------|
| `cr:FileSet` `wsi-files` | every slide in the dataset, by two globs per extension present: one for root-level files and one for nested ones |
| `cr:RecordSet` `slides` | one row per slide, with the fields below |

The fields are `image` (an `sc:ImageObject`), `filename`, `vendor`, `width`, `height`, `level_count`, `mpp_x`, `mpp_y` and `objective_power`. A field is emitted only where the batch has an observed value, so a batch of Leica slides carries no `mpp_x` field describing a measurement none of them made. Every field draws on the `wsi-files` FileSet. As in the OME record set, no `Field.value` is emitted and each header field's description carries the value or range the batch was observed to hold. The record set's description states the slide count, the dimension range, the vendor breakdown and the objective magnifications.

The tile size, the compression, the per-level dimensions and the associated image kinds are read but do not reach the document: they describe how the pixels are stored rather than what was imaged, and there is no field in the record set for them.

Every vendor here describes one thing, a pyramid of a single tissue section, so unlike the OME split in the image handler there is no second collection. A file bearing one of these extensions but no vendor signature is still described, from its TIFF tags, and counted in the vendor breakdown as `no vendor signature`.

A vendor's own XML document is refused on the same terms as OME-XML: a DTD or entity declaration, a document larger than 8 MiB, or text that is not well-formed. A refused file keeps its row and is described from its TIFF tags alone; the refusal count and reasons appear in the record set's description, and a warning naming the file is available to applications that configure logging.

Not read: the pixels, the tile offsets, the ICC profile, the scanner's serial number and scan date, and the annotation files a vendor stores beside the slide.

A wrapped slide (`.svs.gz`, and the other two codecs) is described the same way, at the cost the note under Images gives: a TIFF reader seeks within the file, and a backward seek on a compressed stream can restart decompression from the beginning.

### Not yet supported

| Format | Why |
|--------|-----|
| MIRAX (`.mrxs`) | one slide is a stub file beside a directory of index and data files, and this codebase has no directory-as-one-unit concept |
| Olympus VSI (`.vsi`) | needs a new library, and the pixels live in a companion directory rather than in the named file |
| Zeiss CZI (`.czi`) | not a TIFF container; needs a new library |
| Philips iSyntax (`.isyntax`) | needs the vendor SDK; there is no open reader to depend on |
| OME-Zarr (`.zarr`) | a directory of chunks rather than a file |
| DZI (`.dzi`) | an XML descriptor beside a directory of tiles |

## DICOM

DICOM (`.dcm`, `.dicom`) is the standard format for medical imaging (CT, MRI, PET, etc.). The handler uses `pydicom` with `stop_before_pixels=True` — only the file header is read, so large pixel arrays are never loaded into memory.

Extracted metadata: image dimensions (rows, columns), number of frames, bits allocated per pixel, photometric interpretation, pixel spacing, slice thickness, modality, study/series description, manufacturer, and SOP class UID.

Files with no extension are also accepted if they carry the DICOM magic bytes (`DICM` at byte offset 128), which is common in PACS exports.

All DICOM files in a dataset are grouped into one `cr:FileSet` with a summary `cr:RecordSet` covering modality counts and dimension ranges.

### Whole-slide microscopy

A digital pathology scanner writes DICOM too, under the VL Whole Slide Microscopy Image SOP class (`1.2.840.10008.5.1.4.1.1.77.1.6`). An instance of that class is a slide rather than a cross section, and four further fields are added for it: `wsi_flavor` (the ImageType value 3, one of `VOLUME`, `LABEL`, `OVERVIEW` or `THUMBNAIL`), `total_pixel_matrix_columns` and `total_pixel_matrix_rows` (the size of the whole slide across all tiles), and `container_identifier` (the slide barcode, shared by every instance imaged from one glass slide). The imaged volume in millimetres, the optical path count and the pixel spacing are read too; a slide is multi-frame, so its pixel spacing comes from the shared functional groups rather than from the top-level tag a single-frame CT uses.

The fields are added only when the batch holds a slide, so a batch of cross sections is described exactly as it was before slides were recognised. The record set's description names the slide count and the flavors present, alongside the modality counts. `LABEL` and `OVERVIEW` instances routinely omit the imaged volume and the container id, so each of these values is reported as unstated rather than dropped, keeping the shape of a slide the same across the flavors of one study.

## NIfTI

NIfTI (`.nii`) is the standard format for neuroimaging data (structural MRI, fMRI, CT). The handler uses `nibabel` and reads the header only — the voxel data array is never loaded.

Extracted metadata: spatial dimensions (x, y, z), number of timepoints for 4D volumes, voxel spacing in mm, stored data type, NIfTI version (1 or 2), and repetition time (TR) for fMRI data.

All NIfTI files in a dataset are grouped into one `cr:FileSet` with a summary `cr:RecordSet`. The `tr_seconds` field is only added when at least one 4D volume is present.

## GEO SOFT

SOFT is a metadata and data export format of the NCBI Gene Expression Omnibus.
The handler reads `.soft` family exports and curated DataSet exports such as
`GDS10.soft.gz`. Compression is handled by the shared input layer.

The following record sets describe each file:

| Record set suffix | One row per | Fields |
|-------------------|-------------|--------|
| `_series`, `_samples`, `_platforms`, `_datasets`, `_subsets` | distinct entity of that kind | declared attribute names, with the entity prefix removed |
| `_sample_characteristics` | sample | sample accession and submitter-defined characteristic keys |
| `_series_table`, `_sample_table`, `_platform_table`, `_dataset_table`, etc. | table row | declared table columns |

The `^DATABASE` block is shared GEO boilerplate and is not described. An entity
kind with no attributes produces no attribute record set. Repeated
`^DATASET` declarations for the same accession, separated by `^SUBSET` blocks,
count as one dataset. Unsupported entity kinds cause extraction to fail with a
reason; their metadata is not silently omitted.

Sample characteristics have a separate record set because their keys are
submitter-defined. This also keeps a `title:` characteristic distinct from the
standard `!Sample_title` attribute. Characteristics split on the **first colon**,
with or without whitespace: `tissue:liver` and `tissue: liver` both name `tissue`.
An empty value still names a key. Lines without a nonempty key and colon are
counted and represented by fallback fields such as `characteristics_ch1`.
Channel 1 and channel-less keys keep their bare names; channels 2 and above
always use `ch2_`, `ch3_`, etc. An unrelated sample cannot rename channel 1's
fields. Name collisions receive `__2`, `__3`, etc., preserving each field.

The characteristics record set also includes a scalar text **`geo_accession`**
field. It describes the accession of the enclosing `^SAMPLE = ACCESSION`
declaration, also commonly recorded in `!Sample_geo_accession`. A consumer can
therefore associate that sample with donor keys such as `dbgap_subject_id`
(GSE327347) or `patient id` (GSE335275). This works even when the redundant
`!Sample_geo_accession` attribute is absent. If a submitter uses `geo_accession`
as a characteristic key, that key is preserved and the added sample identifier
uses the next available `geo_accession__N` name; its description identifies its
source. Donor values and accession values are not copied into the manifest.

Tables group by entity kind, optional table title, and exact column signature.
Named series tables, including GSE2034's patient clinical parameters, are
included. Differently titled tables remain separate even with identical
columns. Titles and the deposit's `#COLUMN` descriptions are retained as schema
metadata. Row counts come from `!*_data_row_count` declarations; absent or
invalid counts are reported as unknown. Counts describe the declarations, not
an independent count of the table body.

### Schema metadata

**No data values are emitted.** Attribute and characteristic fields are
`sc:Text`, including numeric-looking donor identifiers. Table columns are typed
using PyArrow and the shared Croissant type mapping. The parser samples up to
500 rows and 1 MiB per table signature, then discards the sample after typing.
Empty trailing cells beyond the header width are ignored; duplicate column
names retain separate positions, types and field identifiers. Type inference
uses sampled rows, so later rows may contain types absent from that sample.
Memory grows with entity metadata and distinct table signatures, not the full
table bodies.

Fields carry `source: {fileObject: …}` and **no `extract`**. Croissant 1.1's
extraction grammar cannot address SOFT entity attributes, and `mlcroissant`
does not read SOFT data. These record sets describe the schema; consumers
reading sample–donor values must parse the source SOFT file within each
`^SAMPLE` block. The sample accession field supplies the schema needed for
that association, without inventing a donor relationship from field names.

Identifiers derive from the logical filename and dataset-relative path.
Compression does not change them. For example, `GSE1_family.soft.gz` produces
`GSE1_family_series` and `GSE1_family_sample_characteristics`. Cross-format
identifier collisions follow the shared generator rules. `encodingFormat` is
`text/x-geo-soft`, with the compression media type added by the input layer.

### Unsupported and incomplete inputs

`GSE*_series_matrix.txt` uses a different grammar and is not supported by this
handler. Neither are `GSM*.txt` or `GPL*.annot`. Renaming a series matrix to
`.soft` does not make it a valid SOFT entity export.

Files without valid `^ENTITY = ACCESSION` declarations, unknown entity kinds,
and mismatched or nested table markers fail extraction with a reason. A table
still open at end of file or undecodable UTF-8 produces a partial-parse warning
on every emitted record set. A complete attribute line at end of file is valid:
attribute blocks have no closing marker. A leading UTF-8 byte order mark is
accepted.

## HDF5 (`.h5`, `.h5ad`, `.hdf5`)

HDF5 is the default container for scientific array data, and the handler reads `h5py`. **Nothing but structure is read** — dataset paths, dtypes, shapes, and the named attributes a recognised layout defines — so describing a 5 GB file costs what describing a 5 MB one with the same structure costs. Attributes are asked for by name and never as a set, because an attribute holds a value: a Keras `model_config` or a MATLAB header can be megabytes.

A file is claimed on the HDF5 signature, which does not have to sit at offset 0: HDF5 permits a user block before the superblock, and MATLAB v7.3 puts a text header in a 512-byte one. The first 8 KiB are searched, at offsets 0 and 512·2ⁿ. A legal file behind a larger user block is reported as having no handler.

### What becomes a record set

Every file gets one of two views, never both.

| Layout | Recognised by | Record sets |
|--------|---------------|-------------|
| AnnData (`.h5ad`) | root `encoding-type: anndata`, or an `obs` and a `var` with no root `encoding-type` at all | `<stem>_obs`, `<stem>_var` |
| 10x feature-barcode matrix | a `matrix` group holding a `features` group and an `indptr` that declares a width | `<stem>_features`, `<stem>_barcodes` |
| 10x, Cell Ranger 2 | any group holding `genes`, `gene_names`, `barcodes`, `data` and an `indptr` that declares a width, under no root `filetype` or `filetype: matrix` | `<stem>_genes`, `<stem>_barcodes`, one pair per genome |
| anything else | — | `<stem>`, one field per leaf dataset |

Nothing is refused. A Keras model, a NetCDF4 file, a MATLAB v7.3 session, an NWB recording and a BigDataViewer volume all fall to the generic view and are described by their datasets — and so does anything that only partly matches a layout: a `matrix` group missing its `features`, either 10x shape whose `indptr` says nothing about how many barcodes there are, or a legacy shape under a `filetype` naming some other format. A partial match is described for what it holds rather than claimed as a layout, since a field standing in for an absent array would name something that is not there.

**A barnyard run gets one table pair per genome.** Cell Ranger 2 wrote one group per reference genome, and a human–mouse mixing experiment has two. Both are wholly present, so both are described, and the record set identifiers carry the genome only when there is more than one: `<stem>_hg19_genes`, `<stem>_mm10_genes`. scanpy and DropletUtils refuse such a file outright and Seurat returns one matrix per genome; the two that refuse do so because their readers return exactly one matrix, which a manifest is not obliged to. Cell Ranger 3 and later express multiple genomes as a `genome` column instead, so this only arises for files written before 2018.

### How to find a field in the file

**Every field's description states its HDF5 path**, so nothing has to be inferred from a naming convention:

```text
Column 'cell_type' at /obs/cell_type in integrated.h5ad
Array /obsm/X_pca, indexed by the obs axis, in integrated.h5ad
Column 'genes' at /GRCh38/genes in filtered_gene_bc_matrices_h5.h5
HDF5 dataset /model_weights/dense/kernel:0 in model.h5
Member 'age' of the record array /obs in legacy.h5ad
```

A column name is not enough on its own, which is why the path is spelled out. `genes` on a Cell Ranger 2 file lives under a group named for the reference genome, and the record-set identifier is derived from the file name, so nothing else in the document would record `GRCh38`. An array is a second case: `X` is indexed by the observation axis but stored at the root, not under `/obs`. Each table's record set also names the group its columns are in (`columns at /matrix/features`).

In the generic view the field's `name` is itself the path. In a recognised layout the `name` is the column, as it is for a CSV.

**No field carries an `extract`.** mlcroissant validates one and cannot execute it: its reader dispatches on `encodingFormat` over CSV, TSV, JSON, Parquet, text, image, audio, video, DICOM and archive types, and has no HDF5 reader at any media type. A field claiming a readable column would be a promise nobody can keep, and the validator would not catch it. The record sets are descriptive, as this tool's Parquet, JSONL and DICOM record sets already are.

### Types, and where they come from

For a recognised layout the AnnData `encoding-type` decides, not the dtype of the object carrying it. A `categorical` is typed from its `categories` and never from its `codes` — the codes are `int8` below 127 categories and `int32` at 40 000, so their width is the encoding rather than the type. A `nullable-integer` is typed from its `values` and not its `mask`.

Older files are read too, because that is what the archives hold. Three vintages wrote a categorical with no encoding of its own, and all three are typed from their labels rather than from their codes:

| Vintage | Where the labels are |
|---|---|
| current | `<column>/categories`, under `encoding-type: categorical` |
| 0.7.0 – 0.7.8 | `<frame>/__categories/<column>`, beside int8 codes |
| pre-0.7 | `uns/<column>_categories`, beside int8 codes |

The 0.7 codes also carry a `categories` attribute holding an object reference. The reference is not what is followed: it names something rather than holding a value, and this reader never resolves one. The sibling's name is what is matched, and anndata's writer composes that exact path. `__categories` itself is never described as a column — anndata reserves the name.

Two more pre-0.7 shapes: `obs` as a compound-dtype dataset gives its members as columns, and a sparse `X` written as a group under `h5sparse_format` / `h5sparse_shape` is one array field, not the three CSR arrays underneath it.

A plain string column arrives in either of two shapes, and which one depends on the pandas version that wrote the file rather than on the anndata version: a `string-array` dataset under pandas 2, a `nullable-string-array` group under pandas 3, where `StringDtype` became the default. Both read as text.

Elsewhere the dtype maps directly, with three cases worth naming. A fixed-length string reports `sc:Text`, which loses the byte width — every string in a Cell Ranger file is one of these. An object or region reference also reports `sc:Text`, because Croissant has nothing better, but its description says it is a reference — `dtype.kind` alone cannot tell one from a variable-length string, and calling it text without saying so would invite a reader to expect labels where there are only pointers. A dataset with a **null dataspace**, which declares no dimensions at all and holds no values, is described as a scalar; that it holds nothing rather than one value is a stated loss.

### Arrays attach to the axis that indexes them

`X` is *n_obs* × *n_var*, so on `<stem>_obs` it is one field of *n_var* values per row; `layers/*` and `obsm/*` join it, and `varm/*` go to `<stem>_var`. A 10x matrix is features × barcodes, so it attaches to `<stem>_features` with the barcode count as its shape. The generic view has no declared axes, so each dataset carries its full shape.

### What is not described

`uns`, `obsp`, `varp` and `raw` are named in the record-set description and not described: the first is arbitrary, the middle two are graphs over one axis rather than per-row features, and `raw` holds a second copy of `X` and `var`. Every recognised layout names its other top-level entries the same way, so nothing in a file goes unmentioned by both views.

The generic view describes at most 300 leaf datasets. When it runs out of room it says the cap was reached and that at least one further dataset is not described — not how many, because counting them means walking the whole file, which is the cost the cap exists to avoid. A file holding exactly 300 claims no omission.

An **external link is never followed**, including a valid one. It can name any HDF5 file on the machine, so following it would describe structure the dataset does not contain and record a path outside its root. The count of links not followed is in the description; the targets are not.

### No value from inside the file

Column names, dataset paths, dtypes, shapes and row counts only. A categorical's labels in particular do not appear: HDF5 guarantees nothing about what they describe, so nothing here can tell an assay vocabulary from a clinical one. Barcodes, feature ids, cell ids and library ids are record-level identifiers and never appear.

### What a wrapper costs

`.h5.gz` is described identically to `.h5`, but not as cheaply. h5py seeks backwards through the file, and a non-seekable codec pays for that by decompressing and discarding everything it skips.

Measured on a 120 MB `.h5ad` holding incompressible data, reading its structure took 2 ms uncompressed, 0.7 s gzipped, 6 s xz-wrapped and 7 s bz2-wrapped. That cost tracks the file's size rather than its structure — uncompressed does not — so a 5 GB `.h5ad.gz` is of the order of half a minute, and the same file bz2-wrapped is several minutes. Uncompressed is worth it for this format, and gzip is worth it over the other two.

## Hidden files and directories

Files inside hidden directories (any path component starting with `.`) are always skipped, and do not appear in the coverage report. Use `--include` and `--exclude` glob patterns to further control which files are processed.
