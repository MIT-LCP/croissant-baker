# HDF5 demo (single-cell + one file from outside single-cell)

A small fixture used by `tests/test_end_to_end.py`. The layout mirrors the
shape a GEO series arrives in, so one bake exercises all three recognised
layouts and the generic view at once.

```
hdf5_demo/
├── GSM000001/filtered_feature_bc_matrix.h5      10x v3 matrix, 40 features x 300 barcodes
├── GSM000002/filtered_feature_bc_matrix.h5      the same, under a second sample directory
├── GSM000003/hgmm_gene_bc_matrices_h5.h5        Cell Ranger 2 barnyard: two genome groups
├── integrated.h5ad                              AnnData, 120 observations x 20 variables
├── environment.hdf5                             NetCDF4-shaped: matches no layout
└── README.md                                    this file, which no handler claims
```

Two samples sharing one basename is what forces the identifier
disambiguation: a bare basename would give both
`filtered_feature_bc_matrix.h5` files the same record set. The barnyard file
is the case where one file gives four record sets rather than two.

## Source

**Synthetic**, and written by `tests/hdf5_fixtures.py` — but written against a
dump of a real file rather than against a reading of the specs. See
"What the fixtures reproduce" below for the dumps, which is where the
differences that matter are recorded.

Neither `anndata` nor `scanpy` is a dependency of this package, so the files
are committed rather than produced at test time: the integration test then
reads frozen bytes rather than whatever the builders happen to emit today.

## What the fixtures reproduce

Reading the published specs was not enough. Every difference below was found
by dumping a real file, and each one changed the handler's behaviour or its
test coverage.

### Cell Ranger 2 (legacy), from `10x_data/1.2.0/filtered_gene_bc_matrices_h5.h5`

```
ROOT ATTRS: CLASS=b'GROUP', FILTERS=65793, PYTABLES_FORMAT_VERSION=b'2.1',
            TITLE=b'', VERSION=b'1.0',
            chemistry_description=b"Single Cell 3' v2",
            filetype=b'matrix',                     <-- see below
            library_ids=(1,) |S4, original_gem_groups=(1,) int64
GRP /hg19_chr21          keys=[barcodes, data, gene_names, genes, indices, indptr, shape]
      attrs: CLASS=b'GROUP', FILTERS=65793, TITLE=b'', VERSION=b'1.0'
DS  /hg19_chr21/barcodes     str(fixed 18)   shape=(12,)
      attrs: CLASS=b'CARRAY', TITLE=Empty(dtype='S1'), VERSION=b'1.1'
DS  /hg19_chr21/data         int32           shape=(12,)
DS  /hg19_chr21/gene_names   str(fixed 14)   shape=(343,)
DS  /hg19_chr21/genes        str(fixed 14)   shape=(343,)
DS  /hg19_chr21/indices      int64           shape=(12,)
DS  /hg19_chr21/indptr       int64           shape=(13,)
DS  /hg19_chr21/shape        int32           shape=(2,)
```

**`filetype = matrix` is the one that mattered.** The handler used to refuse
any file carrying that attribute, believing Cell Ranger 2 wrote none. Every
real one carries it, so the legacy layout matched nothing that was ever
written and all of these files fell to the generic view. 10x's own
`hgmm_1k_raw_gene_bc_matrices_h5.h5` (Cell Ranger 2.1.0) carries the same set.

The rest: strings are **fixed-length** rather than variable-length UTF-8,
`indices` is **int64**, and because Cell Ranger 2 wrote through PyTables every
object carries `CLASS` / `VERSION` / `TITLE` / `FILTERS`. A dataset's `TITLE`
is a **null dataspace** — an attribute holding no value at all — which is the
same shape that, as a *dataset*, used to cost a file its whole description.

### Cell Ranger 3+, from `10x_data/3.0.0/filtered_feature_bc_matrix.h5`

```
ROOT ATTRS: chemistry_description="Single Cell 3' v3", filetype='matrix',
            library_ids=(1,) |S5, original_gem_groups=(1,) int64, version=2
GRP /matrix              keys=[barcodes, data, features, indices, indptr, shape]
DS  /matrix/barcodes                  str(fixed 18)   shape=(1107,)
DS  /matrix/data                      int32           shape=(23866,)
GRP /matrix/features     keys=[_all_tag_keys, feature_type, genome, id, name]
DS  /matrix/features/_all_tag_keys    str(fixed 6)    shape=(1,)
DS  /matrix/features/feature_type     str(fixed 15)   shape=(507,)
DS  /matrix/features/genome           str(fixed 12)   shape=(507,)
DS  /matrix/features/id               str(fixed 15)   shape=(507,)
DS  /matrix/features/name             str(fixed 15)   shape=(507,)
DS  /matrix/indices                   int64           shape=(23866,)
DS  /matrix/indptr                    int64           shape=(1108,)
DS  /matrix/shape                     int32           shape=(2,)
```

No `software_version`, and `version` is an integer. Multi-genome is a `genome`
**column** here, not a second group — which is why the barnyard shape only
ever appears in a legacy file.

### AnnData, from `anndata` 0.13.3 output

Which shape a plain string column takes turns on **pandas**, not on anndata:

| Writer environment | `_index`, plain string columns |
|---|---|
| anndata 0.13.3 + pandas 2.3.3 | `string-array` **datasets** |
| anndata 0.13.3 + pandas 3.0.1 | `nullable-string-array` **groups** |

pandas 3 made `StringDtype` the default for string columns, so anndata
dispatches the same column to the nullable writer. Both are shapes anndata
0.13 emits today, and `write_h5ad` carries both: `_index`, `cell_id` and
`gene_symbol` as `string-array` datasets, `nullable_s` as the group form.

The two older vintages have their own fixtures, written against anndata's
source rather than a dump, because the writers are no longer installable
alongside a current pandas:

- `write_h5ad_h5sparse` — pre-0.7, `X` a group under `h5sparse_format` /
  `h5sparse_shape` (`anndata` 0.6.22 `h5py/h5sparse.py:127`, still read by
  0.7.8 at `_io/h5ad.py:522`).
- `write_h5ad_categories_group` — 0.7.0 to 0.7.8, a categorical as int8 codes
  with a `categories` object reference and a `__categories/<column>` sibling
  (`anndata` 0.7.8 `_io/h5ad.py:281-286`).

### Reproducing the dumps

The real files are not committed. To regenerate the dumps above:

```sh
# real Cell Ranger output, vendored by scanpy under BSD-3
base=https://raw.githubusercontent.com/scverse/scanpy/main/tests/_data/10x_data
curl -sLO $base/1.2.0/filtered_gene_bc_matrices_h5.h5
curl -sLO $base/1.2.0/multiple_genomes.h5
curl -sLO $base/3.0.0/filtered_feature_bc_matrix.h5

# 10x's own barnyard file, Cell Ranger 2.1.0 (20 MB)
curl -sLO https://cf.10xgenomics.com/samples/cell-exp/2.1.0/hgmm_1k/hgmm_1k_raw_gene_bc_matrices_h5.h5
```

Then walk each with `h5py`, printing every object's kind, dtype, shape and
attributes. `tests/test_hdf5_layouts.py` pins what the handler makes of each shape,
so a fixture that drifted from the dump shows up there rather than here.

## Regenerating

```python
from pathlib import Path
from tests import hdf5_fixtures as fx

demo = Path("tests/data/input/hdf5_demo")
for sample in ("GSM000001", "GSM000002"):
    fx.write_tenx(demo / sample / "filtered_feature_bc_matrix.h5", 40, 300)
fx.write_h5ad(demo / "integrated.h5ad", n_obs=120, n_var=20)
fx.write_netcdf(demo / "environment.hdf5")
fx.write_tenx_barnyard(demo / "GSM000003" / "hgmm_gene_bc_matrices_h5.h5", 25, 60)
```

The output is byte-identical between runs, so regenerating an unchanged
fixture leaves the working tree clean.

## The golden

`tests/data/output/hdf5_demo_croissant.jsonld` is the document this dataset
bakes to, and `test_hdf5_demo_generation` reads it rather than overwriting it.
A deliberate change to what the HDF5 handler emits therefore shows up as a diff
in that file.

The comparison resolves FileObject ids to source paths and sorts record sets,
and the test runs in both discovery orders, since `rglob` order is the
filesystem's rather than sorted — comparing the text directly passed on macOS
and failed on Linux. Everything else, field order included, is compared
unchanged. To regenerate, point the CLI at this directory with the same flags
the test uses:

```
croissant-baker -i tests/data/input/hdf5_demo \
  -o tests/data/output/hdf5_demo_croissant.jsonld \
  --name "HDF5 demo (synthetic single-cell series)" \
  --description "Three 10x feature matrices, an integrated AnnData object, and a NetCDF4 file" \
  --url https://example.org/hdf5-demo \
  --license https://creativecommons.org/licenses/by/4.0/ \
  --dataset-version 1.0.0 --date-published 2026-01-01 \
  --creator "croissant-baker test suite"
```
