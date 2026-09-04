# GEO SOFT fixtures

Three NCBI GEO family exports, used by `tests/test_end_to_end.py` and
`tests/test_soft_handler.py`. Two are the smallest real exports in the archive
and are byte-for-byte what GEO serves; the third is row-trimmed, and is the
only one of the three that was edited.

```
geo_soft/
├── GSE1000_family.soft        classic shape: three table signatures, 10 samples
├── GSE327347_family.soft.gz   modern shape: 6 characteristic keys, no tables
├── GSE335275_family.soft.gz   modern shape: 7 characteristic keys, no tables
└── README.md                  this file, which no handler claims
```

The two modern deposits share **no characteristic key** — GSE327347 records
`tissue`, `tissue preservation method`, `treatment`, `dbgap_subject_id`,
`case_diagnosis` and `genotype`; GSE335275 records `gender`, `age`,
`tumor location`, `tumor size`, `mgmt methylation`, `egfr methylation` and
`patient id`. That disagreement is the reason to read the format, and it is
asserted in `test_soft_handler.py`.

## Source

Public domain, from the NCBI Gene Expression Omnibus. Fetched 2026-08-31,
re-verified 2026-09-04.

| File | URL | sha256 as served |
|---|---|---|
| `GSE327347_family.soft.gz` | https://ftp.ncbi.nlm.nih.gov/geo/series/GSE327nnn/GSE327347/soft/GSE327347_family.soft.gz | `4b7d1da5858f44d0eae8b828c414430d145c5ea2ffdd7032b4614eb0a60eb143` |
| `GSE335275_family.soft.gz` | https://ftp.ncbi.nlm.nih.gov/geo/series/GSE335nnn/GSE335275/soft/GSE335275_family.soft.gz | `70c4e22833d06281b1c085a7df8666fcebbde974eb559dcde3b37f001cb550fd` |
| `GSE1000_family.soft.gz` | https://ftp.ncbi.nlm.nih.gov/geo/series/GSE1nnn/GSE1000/soft/GSE1000_family.soft.gz | `fccd0054c5d34b5982d9874e0e196af889e0b7cfcadfb693a166662d488a83fe` |

The first two are committed exactly as served, gzip and all. The third is not:
it is 50.4 MB, 99.96 % of it table bodies, so it is committed row-trimmed and
uncompressed.

## Trimming GSE1000

Every metadata line is kept, and the first ten rows of each of the eleven
tables. The `!*_data_row_count = 22283` declarations are metadata and so
survive intact — which is the point: the declarations now disagree with the
bodies by three orders of magnitude, so a handler that counted rows instead of
reading the declaration reports 10 where the file says 22 283.

```python
import gzip

def trim(src, dst, rows=10):
    in_table = header_seen = False
    kept = 0
    with gzip.open(src, "rb") as fh, open(dst, "wb") as out:
        for raw in fh:
            line = raw.decode("utf-8").rstrip("\r\n")
            marker = line.strip().lower()
            if not in_table:
                out.write(raw)
                if marker.endswith("_table_begin") and "=" not in line:
                    in_table, header_seen, kept = True, False, 0
            elif marker.endswith("_table_end") and "=" not in line:
                out.write(raw)
                in_table = False
            elif not header_seen:
                out.write(raw)
                header_seen = True
            elif kept < rows:
                out.write(raw)
                kept += 1

trim("GSE1000_family.soft.gz", "tests/data/input/geo_soft/GSE1000_family.soft")
```

What the trimmed file still carries, all of it measured on the untrimmed
original: 10 samples over three column signatures — GPL96's 16-column
Affymetrix annotation, `(ID_REF, VALUE, SIG_LOG2)` on one sample and
`(ID_REF, VALUE, SIGNAL_Log2)` on the other nine — 46 `#COLUMN` lines, and no
`!Sample_characteristics` at all, which is what makes it the fixture for "an
entity kind with no fields produces no record set".

## The golden

`tests/data/output/geo_soft_croissant.jsonld` is the document these three files
bake to, and `test_geo_soft_generation` reads it rather than overwriting it, so
a deliberate change to what the handler emits shows up there as a diff. To
regenerate:

```
croissant-baker -i tests/data/input/geo_soft \
  -o tests/data/output/geo_soft_croissant.jsonld \
  --name "GEO SOFT family exports (demo)" \
  --description "Three NCBI GEO family exports: two modern tableless deposits and one classic series with data tables" \
  --url https://www.ncbi.nlm.nih.gov/geo/ \
  --date-published 2026-08-31 \
  --creator "NCBI Gene Expression Omnibus"
```
