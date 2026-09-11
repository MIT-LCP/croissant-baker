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

Not read: `Plane`, `Objective`, `TimeIncrement`, plate and well metadata, pyramid levels, and vendor TIFF extensions such as `.svs`, `.ndpi`, `.scn`, and `.qptiff`.

## DICOM

DICOM (`.dcm`, `.dicom`) is the standard format for medical imaging (CT, MRI, PET, etc.). The handler uses `pydicom` with `stop_before_pixels=True` — only the file header is read, so large pixel arrays are never loaded into memory.

Extracted metadata: image dimensions (rows, columns), number of frames, bits allocated per pixel, photometric interpretation, pixel spacing, slice thickness, modality, study/series description, manufacturer, and SOP class UID.

Files with no extension are also accepted if they carry the DICOM magic bytes (`DICM` at byte offset 128), which is common in PACS exports.

All DICOM files in a dataset are grouped into one `cr:FileSet` with a summary `cr:RecordSet` covering modality counts and dimension ranges.

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
## VCF and gVCF

VCF (`.vcf`) is the variant call format. The handler reads the header and stops
at the first record: nothing below the `#CHROM` line is ever parsed.

A VCF is claimed on its opening `##fileformat=VCF` declaration rather than on
its extension, because `.vcf` is also the vCard extension. A vCard is therefore
reported as a file no handler claimed, and a callset saved under another name is
still described.

Each file produces one record set whose fields are the columns the `#CHROM` line
declares, in that order: `CHROM` (`sc:Text`), `POS` (`cr:Int64`), `ID`
(`sc:Text`), `REF` (`sc:Text`), `ALT` (`sc:Text`, repeated), `QUAL`
(`cr:Float64`), `FILTER` (`sc:Text`, repeated) and `INFO`. A file carrying
genotypes adds `FORMAT` and a single repeated `samples` field standing for the
genotype columns.

`INFO` and `FORMAT` are per-record key-value bags rather than columns of their
own, so each declared key becomes a sub-field of the column that carries it.
The declared `Type` gives the Croissant type (`Integer` to `cr:Int64`, `Float`
to `cr:Float64`, `Flag` to `sc:Boolean`, `String` and `Character` to `sc:Text`),
and any `Number` other than `0` or `1` marks the sub-field repeated, which
covers `A`, `R`, `G`, `.` and literal counts above one. The declared
`Description` becomes the sub-field description; it is a header byte, so
traceability holds.

The declared `##fileformat`, `##reference`, the number of `##contig`
declarations and the sample count are stated in the record set description
rather than in JSON-LD keys no Croissant vocabulary defines. A gVCF is the same
handler and the same shape: `##GVCFBlock` lines or a `NON_REF` alternate allele
are recorded, and the description says so.

Fields carry `source: {fileObject: …}` and **no `extract`**, for the reason
given under GEO SOFT: `mlcroissant` does not read VCF, so a column reference
would be a promise nothing can keep. `encodingFormat` is `text/x-vcf`, with the
compression media type added by the input layer.

A header with no `#CHROM` line declares no columns, and the file is reported
with that reason rather than described.

### Sample identifiers

Sample column names are a manifest of the cohort. They are withheld by default:
the record set states how many samples the file carries, not what they are
called. `--genomic-sample-ids` emits them, mirroring the opt-in shape of
`--count-csv-rows`.

## BCF

BCF (`.bcf`) is the binary form of a VCF: the same header text, followed by
records packed into a binary encoding. A `.bcf` is already a compressed
container, and the input layer does not treat it as one: it is a format, not a
transport wrapper, so the handler decompresses it itself, exactly as the BAM
handler does. It reads the magic, the declared header length and the header
text, then stops. No record is decoded.

A BCF is claimed on the `BCF` every generation of the format opens with, in
either of the two forms the pipeline can hand over: a stream whose payload
starts with it, or one that already starts with it because a second wrapper was
taken off on the way in. Both minor versions of BCF 2 are read, because they
differ in how records are encoded and not in the header. BCF1, samtools' own
first-generation encoding, declares no VCF header text at all: it is claimed so
that it can be reported as the BCF it is, with that as its reason, rather than
left to be reported as a file nothing recognised.

The header is then the VCF handler's, so a callset describes the same way in
either container: one record set per file, whose fields are the columns the
`#CHROM` line declares, with `INFO` and `FORMAT` carrying one sub-field per
declared key, and the `##fileformat`, `##reference`, contig count and sample
count stated in the record set description. `encodingFormat` is
`application/x-bcf`, with the compression media type added by the input layer
when the file arrives under a further wrapper.

Sample column names are withheld under the same `--genomic-sample-ids` opt-in
that governs a plain VCF. Index files (`.csi`) are reported as unsupported;
nothing claims them.

## BAM

BAM (`.bam`) is the compressed binary form of a SAM alignment file. A `.bam` is
already a compressed container, and the input layer does not treat it as one: it
is a format, not a transport wrapper, so the handler decompresses it itself. It
reads the magic, the SAM text header and the reference count, then stops. No
alignment record is read.

A BAM is claimed on that magic, in either of the two forms the pipeline can hand
over: a stream whose payload starts with `BAM\1`, or one that already starts with
it because a second wrapper was taken off on the way in.

From the text header: `@HD` gives the SAM version and sort order, `@SQ` the
number of reference sequences and, from the first, the assembly name; `@RG` the
number of read groups with their sequencing platforms and centres; `@PG` the
program chain in declaration order. The binary `n_ref` that follows the text is
recorded alongside the `@SQ` count.

**No record set is emitted.** Aligned reads are records of a genome, not of a
dataset schema, so a BAM is described as a file: the properties above are stated
in the `description` of its `cr:FileObject`. That description is the one thing
this handler produces; `encodingFormat` is `application/x-bam`, with the
compression media type added by the input layer when the file arrives under one.

`@RG SM` names the sample a read group came from, and the tags together are a
cohort manifest, so they are withheld under the same `--genomic-sample-ids`
opt-in as the VCF sample columns. Index files (`.bai`, `.csi`, `.tbi`) are
reported as unsupported; nothing claims them.

## CRAM

CRAM (`.cram`) stores the same alignment a BAM does, encoded against the
reference the reads were placed on rather than storing their bases. That
reference is not needed to describe the file: the SAM text header sits in the
first block of the first container, and the header is all that is read, so a
CRAM whose reference is a URL nobody can reach is still described in full.

A CRAM is claimed on its magic, the four bytes `CRAM` a file definition opens
with. Reaching the header block after it means walking the first container
header field by field: those fields are written in CRAM's two variable-width
integer encodings, ITF8 and LTF8, so the block behind them cannot be seeked to.
The block is then decoded from raw, gzip, bzip2 or LZMA, whichever it declares;
a gzip block may also be written as a bare zlib stream, and both spellings are
read, as htslib reads them. The decode is bounded by the raw size the block
itself declares: one byte past it is enough to see that the block holds more
than it says, and a block holding anything other than what it declares is
refused rather than expanded. The text it holds is read exactly as a BAM's is:
`@HD` for the SAM version and
sort order, `@SQ` for the reference sequences and, from the first, the assembly
name, `@RG` for the read groups with their platforms and centres, and `@PG` for
the program chain in declaration order. The CRAM version itself is recorded
alongside them.

Four things are refused with a reason rather than guessed at. Major versions
other than 2 and 3: CRAM 1 is obsolete and CRAM 4 changes the integer encodings
a container header is written in, so neither can be walked with this layout. A
file header block coded with rANS, CRAM's own entropy coder, which has no
decoder in the standard library. A block or header text whose declared size is
larger than any real header, which is refused before a byte behind it is read.
And a block whose stream ends early, or that decodes to anything other than the
raw size it declares. CRC32 values are read past rather than checked: what is
described is the header text, and a mismatch is a decoder's corruption report,
not metadata.

**No record set is emitted**, for the reason a BAM emits none: aligned reads are
records of a genome, not of a dataset schema. The properties above are stated in
the `description` of the file's `cr:FileObject`, and `encodingFormat` is
`application/x-cram`, with the compression media type added by the input layer
when the file arrives under one. `@RG SM` sample tags are withheld under the
same `--genomic-sample-ids` opt-in as the BAM tags and the VCF sample columns.
Index files (`.crai`) are reported as unsupported; nothing claims them.

## SAM

SAM (`.sam`) is the text form of the same alignment file, and it opens with the
same header. `@HD` gives the SAM version and sort order, `@SQ` the number of
reference sequences and, from the first, the assembly name; `@RG` the number of
read groups with their sequencing platforms and centres; `@PG` the program chain
in declaration order. Nothing in front of the header says how long it is, so the
read is bounded by the stop at the first line that does not start with `@`: no
alignment record is read, whatever the size of the file. The header is taken a
chunk at a time, and a file that never reaches a line that is not a header line
is reported: a single line above 1 MiB is not a header line, and a header above
64 MiB, the cap the binary containers state their own header length against, is
not a header.

A SAM is claimed on its extension **and** its first header line, and needs both.
A FASTQ opens with `@` as well, so the leading character alone would describe one
as an alignment it is not; and the header shape alone is not what makes a file a
SAM. A `.sam` carrying only alignment records is therefore reported as unclaimed,
which is the honest outcome: with no header it declares no sort order, no
assembly and no read group.

**No record set is emitted**, for the reason BAM emits none: the properties above
are stated in the `description` of the file's `cr:FileObject`. `encodingFormat`
is `text/x-sam`, with the compression media type added by the input layer when
the file arrives under one. `@RG SM` is withheld under the same
`--genomic-sample-ids` opt-in.

## FASTQ

FASTQ (`.fastq`, `.fq`) is the same four lines repeated until the run is
exhausted: a read name, the bases, a `+` separator, and one quality character
per base. The handler reads the first record and stops. Nothing behind it is
opened, so a run of a hundred million reads costs the same read as a run of
one. The record is taken from a bounded prefix of 1 MiB, which holds the
longest read any instrument writes twice over, once as bases and once as
quality scores; a first record that does not end inside it is reported rather
than read for.

The claim needs both the extension and the structure, because neither holds on
its own. `@` opens a record's name line, but it also opens every line of a SAM
header, so the first byte cannot decide; the extension cannot decide either,
because it would claim any text a user happened to name `.fq`. Together they
are the record's own shape: a name line, a sequence, and a separator on the
third line. A `.fastq` whose third line is not `+` is left to the other
handlers and reported with a reason.

What is extracted is the length of the first read. A record whose quality line
does not match its sequence in length, or that has no `+` on its third line, is
a record this handler cannot describe truthfully, and the file is reported with
that reason rather than described. Multi-line FASTQ, where a read is wrapped
across several lines, is deliberately unsupported for the same reason.

**Read names are never reported.** An Illumina read name spells out the
instrument, run, flowcell and lane the read came from, and none of that is
structure. It reaches neither the metadata nor the description, and there is no
flag to turn it on. Records are not counted either: counting them means reading
the whole file, which is the one thing this handler exists not to do.

**No record set is emitted.** Sequencing reads are records of a run, not of a
dataset schema, so a FASTQ is described as a file: the read length is stated in
the `description` of its `cr:FileObject`. `encodingFormat` is `text/x-fastq`,
with the compression media type added by the input layer when the file arrives
under one, so `reads.fastq.gz` is described exactly as `reads.fastq` is.

## FASTA

FASTA (`.fa`, `.fasta`, `.fna`) is a description line followed by sequence,
repeated. The handler reads the first description line and stops there. No
sequence line is ever read: a reference genome is gigabytes of bases and none of
them is metadata.

A FASTA is claimed on its extension **and** on its first byte, and neither half
would do alone. `>` is a single character that a quoted email, a shell
transcript and a diff all begin with, so it is too little to own a file on. The
extension alone would claim any text a user happened to name `.fa`, and past
that first byte the format has no other marker to fall back on: a FASTA is
letters, which is what an unrelated text file is too. A `.fa` that does not open
with `>` is therefore reported as a file no handler claimed, and an empty file
or a bare `>` line is reported with that as its reason rather than described.

What is reported is the format and the encoding. Deliberately not reported:

- **Record names.** A description line names the record, and for a per-sample
  assembly that name is the sample, so it is withheld the way `@RG SM` and the
  VCF sample columns are. Unlike those, it has no opt-in: one line is read, and
  which record it names is not a structural fact about the dataset.
- **The comment text** following the name on the same line, for the same reason.
- **The number of records**, and the sequence lengths. Counting either means
  reading the whole file, which is what header-only reading exists to avoid.

**No record set is emitted.** Bases are records of a genome, not of a dataset
schema, so a FASTA is described as a file: the statement above is carried in the
`description` of its `cr:FileObject`. `encodingFormat` is `text/x-fasta`, with
the compression media type added by the input layer when the file arrives under
one. FASTA has no IANA registration, so the `x-` form follows `text/x-vcf`.

Index and dictionary files (`.fai`, `.dict`, `.gzi`) are reported as unsupported;
nothing claims them.

## MOL

MOL (`.mol`) is an MDL molfile: one molecule, written as a title line, a program
line, a comment line, a counts line, and then the connection table. The handler
reads as far as the counts and stops. The atom block and the bond block below
them are the molecule, not metadata about it.

The counts line is also the only line that says which of the two layouts the
file is written in, and the layouts disagree about where the counts live. A
**V2000** file puts them on the counts line itself, in fixed-width fields: atoms
in columns 1-3, bonds in columns 4-6, the version literal in columns 34-39. A
**V3000** file puts zeros there and writes the real counts further down, on the
`M  V30 COUNTS` line inside `M  V30 BEGIN CTAB`. Both are read; nothing below
either is.

A molfile is claimed on its extension **and** on that version literal, and
neither half would do alone. `.mol` is shared with several unrelated tools that
write a save file under it, so the extension is not evidence on its own; and the
literal is five characters a text file could carry anywhere, so what makes it a
declaration is sitting on the counts line, which is where the extension says to
look. A `.mol` whose fourth line declares neither version is therefore reported
as a file no handler claimed.

What is reported is the molfile version, the title, the atom count and the bond
count. The title may be empty, and a file with a blank first line simply has the
title left out of its description rather than stated as nothing.

**No record set is emitted.** One molecule is a file, not a table: there is no
second row for a record set to hold, and a record set over a single record would
state a schema the file never declares. The statement above is carried in the
`description` of the file's `cr:FileObject`. `encodingFormat` is
`chemical/x-mdl-molfile`, with the compression media type added by the input
layer when the file arrives under one. The `chemical/` family is not
IANA-registered, but it is what toolkits, journals and structure databases have
served molfiles as for decades.

The read is bounded at 64 KiB, which is orders of magnitude more than either
layout's header needs. A V3000 file whose `COUNTS` line does not arrive inside
it is reported with that as its reason, as is a counts line whose atom and bond
fields do not parse.

## SDF

SDF (`.sdf`, `.sd`) is molfile blocks concatenated, each followed by the
depositor's own annotations and closed by a `$$$$` terminator. Those annotations
are what makes an SD file a table where a lone molfile is not: a header line
naming the field between angle brackets, the value on the lines below it, and a
blank line closing it, repeated across a library.

Nothing declares those fields up front, so they are read off the records. Each
file produces one record set with two fields the data items do not name,
`title` (`sc:Text`, the molecule's own name line) and `molfile` (`sc:Text`, the
connection table as text), and then one field per data item, in the order the
sample first saw it.

A field's type is the one every sampled value agrees on: `cr:Int64` when they
are all integers, `cr:Float64` when they are all numbers, `sc:Text` otherwise.
Agreement rather than a majority vote, because a consumer that reads a column as
numeric and meets a compound name in it has been told something untrue. A value
spanning several lines is `sc:Text` whatever those lines hold, and an empty value
is passed over as missing rather than counted as text.

**The field list comes from a sample**, because reading every record of a
screening library means reading the whole file. The sample is the first 100
records or the first 4 MiB, whichever ends first, and nothing past it is read.
The record set description says which it was: *from all 12 records* when the file
ended inside the sample, *from the first 100 records* when it did not. The
molfile version is stated the same way, and reads `mixed` when the sampled
records do not all declare the same one.

Fields carry `source: {fileObject: …}` and **no `extract`**, for the reason given
under VCF: `mlcroissant` does not read SD files, so a column reference would be a
promise nothing can keep. `encodingFormat` is `chemical/x-mdl-sdfile`, with the
compression media type added by the input layer.

An SD file is claimed on its extension **and** on either marker below it: a
fourth line declaring a molfile version, or a `$$$$` terminator in the head.
Either can be the one in reach, and the extension alone is not evidence, since
`.sdf` is also a spatial data format and more than one tool's session file. A
file that states no complete record inside the byte bound is reported with that
as its reason, as is one whose sampled records include a molfile header that
cannot be read.

## SMILES

SMILES (`.smi`, `.smiles`) is one molecule per line: the structure first, then
usually whitespace and a name or a registry identifier, and sometimes further
columns after that. The format declares none of it. There are no magic bytes, no
header line it requires, no delimiter it fixes and no column count it states, so
the layout is read off a **bounded sample** of the head: the first 1000 lines, or
the first 1 MiB, whichever ends first. A library of a million molecules therefore
costs the same read as one of a thousand.

What is reported is the delimiter (`tab` when the sample holds tabs, otherwise
runs of spaces), the column count, and one `sc:Text` field per column. The column
count is the widest line of the sample; a line carrying fewer fields has simply
left the trailing ones off, which is what a molecule with no name looks like, and
that is not an error.

The record set description states the sample the layout came from, either
`from all 42 lines` or `from the first 1000 lines`. A column count read off a
sample is a claim about that sample, and a consumer deciding whether to trust it
needs to know how many lines it was read from.

A SMILES file is claimed on its extension **and** on its first record, and
neither half would do alone. The extension alone would claim any text a user
happened to name `.smi`. The first record alone would not do either, because a
short structure is also a plausible line of many other things. The record is read
as symbols rather than as characters: each letter run outside a bracket atom has
to spell an atom of the OpenSMILES organic subset, so `CCO` and `c1ccccc1` are
structures while `ethanol` and `SMILES` are not. Lines opening with `#` are
comments in the dialects that have one, and are skipped before the check; `#` is
a triple bond, and no structure opens with a bond.

**Column names come from a header line when the file wrote one.** A header is
detected, not declared: a first record whose first field is no structure,
followed by one whose first field is, is a file that named its columns, and the
names are taken from it. Otherwise the columns are named by position, `smiles`
and `name` and then `column_3`, `column_4`, because the file states nothing for
them to be named after. A file whose first record is no structure and whose
second is none either is reported with that as its reason rather than described
as a molecule table it is not, as is an empty file and one holding only comments.

**Nothing from a data line is emitted.** A structure is the data, and the name
beside it is a depositor's label for a compound; neither reaches the metadata,
and the column names from a header line are the only text out of the file that
does. Molecules are not counted either: counting them means reading the whole
file.

`encodingFormat` is `chemical/x-daylight-smiles`, with the compression media type
added by the input layer when the file arrives under one. SMILES has no IANA
registration, so the media type follows the `chemical/x-*` family cheminformatics
tools register theirs under.

## PDB

A wwPDB structure file (`.pdb`, `.ent`) is fixed-column text: eighty columns per
record, each named by columns 1 to 6, with the title section written before the
coordinates. The handler reads that title section and stops at the first
`MODEL`, `ATOM` or `HETATM` record, so a structure of a hundred thousand atoms
costs the same read as a fragment of three. No coordinate line is ever read.

What is reported, each field from the columns the format fixes it to:

- **ID code, classification and deposition date**, from `HEADER`. The date is
  reported as written (`12-JAN-98`); converting it would invent a century the
  file does not state.
- **Title**, with its continuation lines joined into one run of words.
- **Experimental methods**, from `EXPDTA`, split on the semicolons a structure
  determined two ways separates them with.
- **Resolution** in angstroms, from `REMARK   2 RESOLUTION.`, when that remark
  carries a number. A structure determined without diffraction writes
  `NOT APPLICABLE` there, and then no resolution is reported.
- **Chain count**, from the `CHAIN:` tokens of the `COMPND` specification list,
  or from the SEQRES chain column when `COMPND` names none.
- **Model count**, from `NUMMDL`, and **keywords**, from `KEYWDS`.

A PDB file is claimed on its extension **and** on its first record name, and
neither half would do alone. `.pdb` is also the Microsoft program database, a
binary of debugging symbols that carries no structure and must not be described
as one; six columns of upper-case letters are a shape any text file can wear, so
the record name cannot own a file on its own either. `.ent` is the second
extension, because that is what the RCSB archive calls its own copies of an
entry (`pdb1abc.ent.gz`). A file whose header runs past the cap without reaching
a coordinate record, or whose first line runs to kilobytes with no line ending,
is reported with that as its reason rather than read on for.

A file carrying no `HEADER` record, a fragment written by a modelling tool,
which opens at `ATOM`, is still described, with the fields it has; the
description then says the header carries no ID code.

Deliberately not reported:

- **Depositors.** The `AUTHOR` record names people. It is bibliographic rather
  than structural, and the dataset's own creator is a command-line input rather
  than something read out of a file.
- **The chain identifiers themselves.** How many chains a structure holds is
  structure; which letters they were given is not.
- **Atom counts, coordinates and B-factors.** Reaching any of them means reading
  the coordinate section, which is what header-only reading exists to avoid.

**No record set is emitted.** Atom records are records of a molecule, not of a
dataset schema, so a structure is described as a file: the statement above is
carried in the `description` of its `cr:FileObject`. `encodingFormat` is
`chemical/x-pdb`, with the compression media type added by the input layer when
the file arrives under one. PDB has no IANA registration; `chemical/x-pdb` is
the spelling the chemical MIME family gave it, and the one the archive and the
molecular viewers use.

mmCIF/PDBx, the format the archive now treats as primary, is described by the
handler in the next section.

## mmCIF and CIF

A CIF (`.cif`, `.mmcif`) is a syntax before it is a subject. One file holds one
or more `data_` blocks, each a list of `_name value` items and `loop_` tables,
and the same grammar carries a protein deposit, a small-molecule structure, a
powder pattern and a dictionary. Two of those dialects are described here:

- **PDBx/mmCIF**, what the wwPDB archive now treats as primary, and the only
  form that can hold a structure too large for PDB's eighty columns. Its items
  carry a category prefix: `_entry.id`, `_struct.title`.
- **Core CIF**, what the COD, the CSD and the IUCr journals ship a small
  molecule in. Its item names carry no category: `_cell_length_a`.

Behind either sits the `_atom_site` table, which is the file: an archive entry
is megabytes of coordinates behind a header of a few kilobytes. The handler
reads the items and loops in front of that table and stops at the `loop_` whose
first item name begins `_atom_site.` or `_atom_site_`. No coordinate row is ever
read.

From a PDBx block:

- **Entry id**, from `_entry.id`, and **deposition date**, from
  `_pdbx_database_status.recvd_initial_deposition_date`, reported as written.
- **Title**, from `_struct.title`. A title written as a multi-line `;` text
  field is joined into one run of words; the column the file wrapped it at is
  not part of what it says.
- **Experimental methods**, from `_exptl.method`, whether the file wrote one as
  a single item or several as a loop.
- **Resolution** in angstroms, from `_refine.ls_d_res_high`, or from
  `_em_3d_reconstruction.resolution` for a structure determined by microscopy.
  An entry writing either as `?` states that the value is unknown, and then no
  resolution is reported.
- **Model count**, from `_pdbx_nmr_ensemble.conformers_submitted_total_number`.
- **Polymer entity count**, the rows of `_entity_poly`, and **chain count**, the
  distinct strand identifiers those entities name in
  `_entity_poly.pdbx_strand_id`. That is the same count the PDB handler reports
  for the same entry, so the two describe a structure alike.
- **Asym unit count**, the rows of `_struct_asym`, reported beside the chain
  count rather than as it. An asym unit is not a chain: a deposit gives one to
  every copy of every ligand and one to its ordered solvent, so a four-chain
  haemoglobin carries nine. It is stated in the description only where it
  differs from the chain count, and it stands in for the chain count only in a
  block that carries no polymer entity at all.
- **Classification**, from `_struct_keywords.pdbx_keywords`, **keywords**, from
  `_struct_keywords.text` split on commas, and the **dictionary** the file
  declares it conforms to, from `_audit_conform`.

From a core CIF block:

- **Data block name**, **chemical name**, from `_chemical_name_common` or
  `_chemical_name_systematic`, and **formula**, from `_chemical_formula_sum`.
- **Space group**, from `_space_group_name_H-M_alt` or, in files written before
  the category was renamed, `_symmetry_space_group_name_H-M`.
- **Cell**, the three edges and three angles, reported together or not at all: a
  cell is one description of one lattice, and three edges without their angles
  do not describe it. A value carries its standard uncertainty in parentheses,
  `10.1234(4)`, and the uncertainty is a second value about the edge rather than
  part of it, so it is stripped.
- **Wavelength**, from `_diffrn_radiation_wavelength`.

The dialect is decided by the block, not by the extension, which the two share.
A block is PDBx when it states `_entry.id`, names a dictionary in
`_audit_conform.dict_name`, or carries any `_struct.` item; it is a small
molecule when, PDBx having been ruled out, it states `_cell_length_a` or
`_chemical_formula_sum`. A block that is neither, a dictionary or a powder
pattern, is reported with that as its reason rather than described from the few
items the two dialects happen to share.

A CIF is claimed on its extension **and** on its opening a `data_` block, and
neither half would do alone. `.cif` is also the Windows compiled-installation
file, a setup-time binary that carries no structure and must not be described as
one, and three letters generic enough that other tools have taken them too; a
line opening `data_` is a shape any text file can wear, so it cannot own a file
on its own either. The comment banner a COD or CSD deposit opens with is skipped
to find that line, within the few kilobytes the claim reads.

Deliberately not reported:

- **Depositors.** `_audit_author` names people. It is bibliographic rather than
  structural, and the dataset's own creator is a command-line input rather than
  something read out of a file.
- **Coordinates, atom counts and B-factors.** Reaching any of them means reading
  the coordinate table, which is what header-only reading exists to avoid.
- **The blocks after the first.** A file holding several is described by its
  first, because reaching the second means reading past a coordinate table.

The tokenizer covers the CIF 1.1 subset the two dialects are written in:
comments, single items, values quoted under either quote, multi-line `;` text
fields, and loops. It does not cover `save_` frames, which belong to dictionary
files, the `global_` and `stop_` reserved words, or the CIF 2.0 list and table
values; any of those is read as an ordinary value, which is why a dictionary
file is refused for its categories rather than described badly. A text field
that never closes, a single line running past a megabyte with no line ending in
it, and a header that passes the byte cap without reaching a coordinate table,
are each reported with that as the reason rather than read on for. The line cap
is the second one because the header cap does not bound a file with no line
ending: a reader assembling a line holds what it has read, so such a file costs
the cap in memory and the square of it in copying on the way to it.

**No record set is emitted.** Coordinate rows are records of a molecule, not of
a dataset schema, so a structure is described as a file: the statement above is
carried in the `description` of its `cr:FileObject`. `encodingFormat` is
`chemical/x-mmcif` for a PDBx block and `chemical/x-cif` for a small-molecule
one, decided per file, with the compression media type added by the input layer
when the file arrives under one. Neither has an IANA registration; both
spellings come from the chemical MIME family that gave `chemical/x-pdb` its
name.

## XYZ

XYZ (`.xyz`) is an atom count, a comment line, and then one line of `symbol x y
z` per atom; a trajectory or a multi-structure export repeats that frame back to
back. The handler reads the first frame's header and stops there. No further
frame is opened and no coordinate is read: the geometry is the data, and a
molecular dynamics run is gigabytes of it.

An XYZ is claimed on its extension **and** on the shape of its head, and neither
half would do alone. A leading integer on a line of its own is also how a
numbered list, a record count and a line-oriented log all open, so it is too
little to own a file on; the extension alone would claim anything a user
happened to name `.xyz`, which several unrelated formats have. Together they are
a frame: a count, a comment line that may say anything at all, and under them a
line of a symbol and three numbers. A `.xyz` whose first line is not a count, or
whose third line is not an atom line, is therefore reported as a file no handler
claimed.

What is reported is the first frame's atom count and its comment line, verbatim
and stripped of surrounding whitespace. The comment is usually a title and is
often empty, and either way it is bytes the file states rather than a reading of
them. When it carries the extended-XYZ `Properties=species:S:1:pos:R:3` term,
the file is reported as extended XYZ and the property names in that term are
reported with it: they are the columns the file declares its atom lines to
carry.

Deliberately not reported:

- **The number of frames.** Counting them means reading the whole file, which is
  what header-only reading exists to avoid. One structure and a million-frame
  trajectory cost the same read.
- **The coordinates**, and anything derived from them: no cell, no bounding box,
  no per-element tally. The first atom line is looked at only to confirm the
  frame is one, and is then discarded.
- **What the extended-XYZ columns hold.** The names come off the `Properties=`
  declaration; the values under them are never parsed.

A frame of zero atoms is legal and is described as one, because a trajectory
writer emits it for an empty cell. A header that declares atoms with no atom
line under it, an empty file, and a first atom line that is not a symbol and
three numbers are each reported with that as the reason rather than described.

**No record set is emitted.** Atoms are records of a structure, not of a dataset
schema, so an XYZ is described as a file: the statement above is carried in the
`description` of its `cr:FileObject`. `encodingFormat` is `chemical/x-xyz`, with
the compression media type added by the input layer when the file arrives under
one. XYZ has no IANA registration; `chemical/*` is the family the chemistry
tools have used for these files for decades, and the `x-` form marks it as
unregistered the way `text/x-fasta` does.

## Hidden files and directories

Files inside hidden directories (any path component starting with `.`) are always skipped, and do not appear in the coverage report. Use `--include` and `--exclude` glob patterns to further control which files are processed.
