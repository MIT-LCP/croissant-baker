# Structural biology demo (structures, maps, reflections and metadata)

A small fixture used by `tests/test_end_to_end.py`. The layout is what a
deposit looks like when it is organised by experiment, so one bake exercises
all six structural biology handlers and the paths that only appear when they
sit beside one another.

```
structural_biology_demo/
├── entries/1abc.pdb                X-ray entry, PDB records
├── entries/1abc.cif                the same entry as mmCIF
├── entries/model.cif.gz            a cryo-EM entry, gzip-wrapped
├── chemistry/glycine.cif           small-molecule CIF, core dictionary
├── chemistry/ligands.sdf           two molecules with property tags
├── chemistry/ethanol.mol2          Tripos MOL2
├── dictionaries/audit.cif          a CIF holding no structure at all
├── cryoem/job001/run_data.star     RELION optics and particles blocks
├── cryoem/job002/run_data.star     the same name under a second job
├── cryoem/postprocess.star         a block of pairs beside an FSC loop
├── cryoem/tomogram.mrc             MRC volume
├── cryoem/particles.mrcs           MRC image stack
├── cryoem/tilt_series.mdoc         SerialEM tilt series
├── xray/native.mtz                 MTZ reflections
└── README.md                       this file, which no handler claims
```

Four things are here because they are the cases that go wrong quietly:

- **Two `run_data.star` under different jobs.** A bare basename would give both
  files the same record set identifiers; the parent directory disambiguates
  them into `job001__run_data_particles` and `job002__run_data_particles`.
- **`dictionaries/audit.cif` beside the entries.** One suffix covers three
  dictionaries, and this one holds no atoms. It is described as the two tables
  it is, and the structure FileSet excludes it by name so that `**/*.cif` does
  not count it as a structure.
- **`entries/model.cif.gz`.** Compression is transport: the entry is described
  exactly as an uncompressed one, and only the distribution entry differs.
- **An mmCIF and a PDB of one entry, in one directory.** Two files of different
  size are two files, so both are described and both join the structure record
  set.

## Source

**Synthetic**, and written by `tests/structural_biology_fixtures.py`. Nothing
here is derived from a deposited file: the handlers describe headers, block
structure and column names, so a map of six sections says what a map of six
hundred would, and the entries are three residues rather than three hundred.

The text formats are literals in that module, the MRC headers are packed with
`struct`, and the two files a real writer would produce go through gemmi: the
mmCIF entries and the MTZ. gemmi stamps neither a date nor a UUID, so its
output is the same on every run.

## Regenerating

```python
from pathlib import Path
from tests.structural_biology_fixtures import write_demo

write_demo(Path("tests/data/input/structural_biology_demo"))
```

The output is byte-identical between runs, so regenerating an unchanged fixture
leaves the working tree clean. `README.md` is not written by `write_demo`,
because it documents it.

## The golden

`tests/data/output/structural_biology_demo_croissant.jsonld` is the document
this dataset bakes to, and `test_structural_biology_demo_generation` reads it
rather than overwriting it. A deliberate change to what any of the six handlers
emits therefore shows up as a diff in that file.

The comparison resolves FileObject ids to source paths and sorts record sets,
and the test runs in both discovery orders, since `rglob` order is the
filesystem's rather than sorted. Everything else, field order included, is
compared unchanged. To regenerate, point the CLI at this directory with the
same flags the test uses:

```
croissant-baker -i tests/data/input/structural_biology_demo \
  -o tests/data/output/structural_biology_demo_croissant.jsonld \
  --name "Structural biology demo (synthetic structures, maps and metadata)" \
  --description "PDB and mmCIF entries, a small-molecule CIF, a CIF dictionary, RELION STAR files, MRC maps, an MTZ, a SerialEM mdoc and a small-molecule library" \
  --url https://example.org/structural-biology-demo \
  --license https://creativecommons.org/licenses/by/4.0/ \
  --dataset-version 1.0.0 --date-published 2026-01-01 \
  --creator "croissant-baker test suite"
```
