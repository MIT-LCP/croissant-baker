# Getting Started

## Installation

```bash
pip install croissant-baker
```

or with [uv](https://docs.astral.sh/uv/):

```bash
uv add croissant-baker
```

Requires Python 3.10 or later.

To describe PDB, mmCIF or STAR files, add the structural biology extra, which
brings in [gemmi](https://gemmi.readthedocs.io/):
`pip install "croissant-baker[structural-biology]"` (or
`uv add "croissant-baker[structural-biology]"`). Without it those files are
reported with that hint rather than described; every other format, MRC, MTZ,
mdoc, SDF, MOL and MOL2 included, is read either way.

## Your first metadata file

Try it on the bundled MIMIC-IV Demo test data (included in the repo):

```bash
croissant-baker \
  --input tests/data/input/mimiciv_demo/physionet.org/files/mimic-iv-demo/ \
  --creator "Alistair Johnson,aewj@mit.edu,https://physionet.org/" \
  --creator "Tom Pollard,tpollard@mit.edu,https://physionet.org/" \
  --name "MIMIC-IV Clinical Database Demo" \
  --description "Demo subset of MIMIC-IV containing 100 de-identified patients from Beth Israel Deaconess Medical Center (2008-2019)" \
  --url "https://physionet.org/content/mimic-iv-demo/2.2/" \
  --license "https://opendatacommons.org/licenses/odbl/1-0/" \
  --dataset-version "2.2" \
  --date-published "2023-06-22" \
  --output mimic-iv-demo-croissant.jsonld
```

This scans the directory, infers types from every CSV and Parquet file it finds, and writes a validated Croissant JSON-LD file.

## Sample output (abridged)

```json
{
  "@context": {"@vocab": "https://schema.org/", "cr": "http://mlcommons.org/croissant/1.0/"},
  "@type": "sc:Dataset",
  "name": "MIMIC-IV Clinical Database Demo",
  "description": "Demo subset of MIMIC-IV...",
  "license": "https://opendatacommons.org/licenses/odbl/1-0/",
  "creator": [
    {"@type": "sc:Person", "name": "Alistair Johnson", "email": "aewj@mit.edu"}
  ],
  "distribution": [
    {
      "@type": "cr:FileObject",
      "@id": "hosp/admissions.csv",
      "name": "admissions.csv",
      "encodingFormat": "text/csv",
      "sha256": "a1b2c3..."
    }
  ],
  "recordSet": [
    {
      "@type": "cr:RecordSet",
      "@id": "admissions",
      "field": [
        {"@type": "cr:Field", "name": "subject_id", "dataType": "sc:Integer"},
        {"@type": "cr:Field", "name": "admittime", "dataType": "sc:Text"}
      ]
    }
  ]
}
```

## Validate an existing file

```bash
croissant-baker validate mimic-iv-demo-croissant.jsonld
# Valid! Croissant file passed validation
# Dataset: MIMIC-IV Clinical Database Demo
# Files: 16
# Record sets: 16
```

## Dry run

Preview which files would be processed without writing anything:

```bash
croissant-baker --input ./my-dataset --dry-run
# Dry run: 4 file(s) would be processed in './my-dataset':
#   patients.csv
#   vitals.parquet
#   images/scan_001.png
#   images/scan_002.png
```

## Next steps

- [Supported Formats](user-guide/supported-formats.md) — file types handled and how
- [Examples](user-guide/examples.md) — more command invocations
- [RAI Metadata](user-guide/rai.md) — adding responsible AI fields
- [CLI Reference](reference/cli.md) — all flags and options
