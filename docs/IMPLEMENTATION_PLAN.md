# Structural biology file support (issue #138)

Package `src/croissant_baker/handlers/structural_biology/`, six handlers, one
new runtime dependency (`gemmi`, for PDB, mmCIF and STAR parsing). Binary
headers (MRC and MTZ) and the text formats (mdoc, SDF, MOL, MOL2) are parsed
without any library. Trajectory formats (XTC, TRR, DCD) are out of scope: the
libraries that read them need Python 3.11 or later and this package supports
3.10.

Every stage follows red, green, refactor: a failing test first, the minimal
code to pass it, then cleanup with the suite green.

## Stage 1: Macromolecular structures (PDB, mmCIF)
**Goal**: `StructureHandler` in `structure_handler.py` claiming `.pdb`,
`.ent`, `.cif`, `.mmcif`; reads through gemmi from bytes; one FileSet plus one
per-file-record RecordSet (entry id, title, method, resolution, cell, space
group, model, chain, residue and atom counts). Small-molecule CIF (cell,
space group, formula, site count) described from the same handler.
**Success Criteria**: header fields match written fixtures; garbage raises
`ValueError` naming the file; upper-case suffix claims; empty batch describes
nothing.
**Tests**: `tests/test_structure_handler.py`
**Status**: Complete

## Stage 2: STAR files
**Goal**: `cif.py` (shared CIF and STAR document description through
`gemmi.cif`: blocks, loops, pair blocks, column types inferred from values)
and `STARHandler` in `star_handler.py` claiming `.star`; one RecordSet per
data block per file with typed columns.
**Success Criteria**: RELION optics and particles blocks become two record
sets with the right column names and types; a pair block becomes a one-row
record set; empty document raises `ValueError` naming the file.
**Tests**: `tests/test_star_handler.py`, `tests/test_structural_cif.py`
**Status**: Complete

## Stage 3: MRC/CCP4 maps and MTZ reflections
**Goal**: `MRCHandler` in `map_handler.py` (`.mrc`, `.mrcs`, `.map`,
`.ccp4`; 1024-byte header parsed with `struct`, both endiannesses, mode to
dtype, voxel size, stack versus volume) and `MTZHandler` in `mtz_handler.py`
(`.mtz`; header records parsed from the offset in the first word; one
RecordSet per file with one typed field per column, cell, space group,
resolution, datasets).
**Success Criteria**: values match files written by gemmi and by hand;
`.map` needs the `MAP ` signature; MTZ needs the `MTZ ` signature.
**Tests**: `tests/test_map_handler.py`, `tests/test_mtz_handler.py`
**Status**: Complete

## Stage 4: SerialEM mdoc and small molecules
**Goal**: `MdocHandler` in `mdoc_handler.py` (`.mdoc`; global keys,
sections, per-section keys typed) and `SmallMoleculeHandler` in
`molecule_handler.py` (`.sdf`, `.mol`, `.mol2`; molecule count, atom counts,
SDF property tags as typed fields).
**Success Criteria**: section and tag schemas match hand-written fixtures;
malformed input raises `ValueError` naming the file.
**Tests**: `tests/test_mdoc_handler.py`, `tests/test_molecule_handler.py`
**Status**: Complete

## Stage 5: Integration, docs, golden end-to-end
**Goal**: register the six handlers, add `SAMPLES` entries so the contract
sweep and compression matrix cover them, generic CIF fallback in
`StructureHandler` via `cif.py`, README and user-guide rows and sections,
regenerated formats table, demo dataset with committed golden and a
discovery-order-independent end-to-end test, DEVELOPMENT.md dataset list.
**Success Criteria**: full suite green; `docs/generate.py` output committed;
README lists every new extension.
**Tests**: contract sweep, `tests/test_end_to_end.py`
**Status**: In Progress
