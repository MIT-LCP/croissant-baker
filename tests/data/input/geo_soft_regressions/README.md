# GEO SOFT review regression fixtures

Small extracts of public NCBI GEO exports, downloaded 2026-09-09. Attribute
lines, table markers, headers and column descriptions are unchanged. Each
table retains its first three rows. GSE2034 retains the DATABASE and SERIES entities;
its platform and sample entities are covered by the other GEO fixtures.

| Fixture | Purpose | Original download |
|---------|---------|-------------------|
| `GSE2034_series.soft` | Named expression and patient clinical tables; extra trailing tabs in expression rows | [GSE2034 family](https://ftp.ncbi.nlm.nih.gov/geo/series/GSE2nnn/GSE2034/soft/GSE2034_family.soft.gz) |
| `GDS10.soft` | Dataset attributes, all 12 subsets, resumed dataset declaration and expression table | [GDS10](https://ftp.ncbi.nlm.nih.gov/geo/datasets/GDSnnn/GDS10/soft/GDS10.soft.gz) |

SHA-256 of the downloaded gzip files before trimming:

- GSE2034: `1fa927dc72870434ba799041dcb6dcd292cbb77ab1eac203ea8e5d51272cd279`
- GDS10: `d5587f15c9457ed6db6fd59cdb81a0f33f05cacb6595c7caa2dff53e75b07a25`

To reproduce, decompress as UTF-8 text with universal newline handling. For
GSE2034, stop at the first `^PLATFORM` line. Match table control lines with
`![a-z]+_table_(begin|end)(?: *=.*)?`, case-insensitively. Keep every line outside
a table and each table's header plus first three rows; keep every end marker.
Write the result with LF line endings. Do not change metadata declarations to
match the trimmed row counts.
