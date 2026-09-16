"""Structural biology files to describe, written byte for byte by hand.

Every builder here is deterministic: the same call writes the same bytes, so
regenerating a committed fixture leaves the working tree clean and the golden
document stays comparable. That rules out anything that stamps a date or a
UUID, which is why the text formats are written as literals and the two binary
headers are packed with :mod:`struct` rather than produced by a writer.

The two exceptions read through gemmi, which writes deterministically: an
mmCIF comes from a parsed PDB entry, and the MTZ from ``gemmi.Mtz``. Both are
what a real writer emits, which is the point of using them.

The files are small on purpose. Nothing here is read for its content: the
handlers describe headers, block structure and column names, so a map of six
sections says exactly what a map of six hundred would.
"""

from __future__ import annotations

import gzip
import struct
import tempfile
from pathlib import Path

#: An X-ray entry: two protein chains, one water, in an orthorhombic cell.
PDB_ENTRY = """\
HEADER    HYDROLASE                               01-JAN-00   1ABC
TITLE     A SMALL TEST STRUCTURE
EXPDTA    X-RAY DIFFRACTION
REMARK   2 RESOLUTION.    1.80 ANGSTROMS.
CRYST1   40.000   50.000   60.000  90.00  90.00  90.00 P 21 21 21    4
ATOM      1  N   ALA A   1      11.104   6.134  -6.504  1.00 20.00           N
ATOM      2  CA  ALA A   1      11.639   6.071  -5.147  1.00 20.00           C
ATOM      3  C   ALA A   1      12.253   4.699  -4.914  1.00 20.00           C
HETATM    4  O   HOH A 100      20.000  20.000  20.000  1.00 30.00           O
ATOM      5  N   GLY B   1      15.104   6.134  -6.504  1.00 20.00           N
ATOM      6  CA  GLY B   1      15.639   6.071  -5.147  1.00 20.00           C
END
"""

#: A second entry, solved by cryo-EM: no cell worth the name, a coarser
#: resolution, and one chain. A batch holding both reports two methods and a
#: resolution range rather than a single value.
EM_ENTRY = """\
HEADER    MEMBRANE PROTEIN                        01-JAN-00   2EMD
TITLE     A SMALL CRYO-EM STRUCTURE
EXPDTA    ELECTRON MICROSCOPY
REMARK   2 RESOLUTION.    3.40 ANGSTROMS.
ATOM      1  N   SER A   1       3.104   1.134  -2.504  1.00 40.00           N
ATOM      2  CA  SER A   1       3.639   1.071  -1.147  1.00 40.00           C
ATOM      3  C   SER A   1       4.253   2.699  -0.914  1.00 40.00           C
END
"""

#: A core-dictionary CIF: underscore-only tags, one molecule in a cell.
SMALL_MOLECULE_CIF = """\
data_glycine
_chemical_name_systematic         'aminoacetic acid'
_chemical_formula_sum             'C2 H5 N O2'
_cell_length_a                    5.1054
_cell_length_b                    11.9688
_cell_length_c                    5.4645
_cell_angle_alpha                 90.0
_cell_angle_beta                  111.78
_cell_angle_gamma                 90.0
_symmetry_space_group_name_H-M    'P 1 21/n 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
N1 N 0.0800 0.1150 0.1250
C1 C 0.1500 0.2250 0.2500
C2 C 0.2500 0.3350 0.3750
O1 O 0.3500 0.4450 0.5000
O2 O 0.4500 0.5550 0.6250
"""

#: A CIF document that is neither: valid syntax, an audit block of pairs and a
#: loop of authors, and not one atom. A dictionary or a deposition log looks
#: like this, and the handler describes it as the two tables it is.
GENERIC_CIF = """\
data_audit
_audit_creation_method    'written by hand'
_audit_creation_date      2026-01-01
_audit_revision_count     2

loop_
_audit_author_name
_audit_author_address
'Rivera, K.'  'Cambridge, MA'
'Okafor, N.'  'Cambridge, MA'
"""

#: A RELION particle table: an optics group, then three particles. The columns
#: are the ones whose inferred types differ from one another.
PARTICLES_STAR = """\
# version 30001

data_optics

loop_
_rlnOpticsGroupName
_rlnOpticsGroup
_rlnMicrographPixelSize
_rlnVoltage
_rlnSphericalAberration
_rlnImageSize
opticsGroup1 1 1.350000 300.000000 2.700000 256

# version 30001

data_particles

loop_
_rlnCoordinateX
_rlnCoordinateY
_rlnImageName
_rlnClassNumber
_rlnAngleRot
1104.500000 998.000000 000001@stack.mrcs 1 -34.500000
 998.000000 512.250000 000002@stack.mrcs 2  12.750000
 512.250000 220.000000 000003@stack.mrcs 1 178.000000
"""

#: The second job's copy, which differs only in what it measured. Two RELION
#: jobs both write ``run_data.star``, and that is the collision the demo is
#: there to exercise.
PARTICLES_STAR_SECOND = """\
# version 30001

data_optics

loop_
_rlnOpticsGroupName
_rlnOpticsGroup
_rlnMicrographPixelSize
_rlnVoltage
_rlnSphericalAberration
_rlnImageSize
opticsGroup1 1 1.350000 300.000000 2.700000 256

# version 30001

data_particles

loop_
_rlnCoordinateX
_rlnCoordinateY
_rlnImageName
_rlnClassNumber
_rlnAngleRot
 220.000000 118.000000 000001@stack.mrcs 3  84.250000
 118.500000 640.000000 000002@stack.mrcs 3 -12.000000
"""

#: The other shape RELION writes: a block of tag/value pairs, which is one row,
#: beside a resolution curve, which is many.
POSTPROCESS_STAR = """\
data_general

_rlnFinalResolution                    3.200000
_rlnBfactorUsedForSharpening         -85.000000
_rlnParticleBoxFractionSolventMask     0.830000
_rlnRandomiseFrom                      6.500000

data_fsc

loop_
_rlnSpectralIndex
_rlnResolution
_rlnFourierShellCorrelationCorrected
0 0.000000 1.000000
1 0.007813 0.998000
2 0.015625 0.981000
3 0.023438 0.902000
"""

#: A tilt series as SerialEM records one: two title lines, the acquisition's
#: globals, then a section per recorded image.
TILT_SERIES_MDOC = """\
[T = SerialEM: Digitized on a Titan Krios]
[T = Tilt axis angle = 85.30]

PixelSpacing = 1.35
Voltage = 300
ImageFile = tilt_series.mrc
ImageSize = 4096 4096
DataMode = 6

[ZValue = 0]
TiltAngle = -60.00
StagePosition = 122.450 -87.300
StageZ = -12.7500
Magnification = 33000
ExposureDose = 3.0
SubFramePath = frames/tilt_000.tif
NumSubFrames = 10

[ZValue = 1]
TiltAngle = -57.00
StagePosition = 122.455 -87.310
StageZ = -12.7480
Magnification = 33000
ExposureDose = 3.0
SubFramePath = frames/tilt_001.tif
NumSubFrames = 10

[ZValue = 2]
TiltAngle = -54.00
StagePosition = 122.460 -87.315
StageZ = -12.7460
Magnification = 33000
ExposureDose = 3.0
SubFramePath = frames/tilt_002.tif
NumSubFrames = 10
"""


def _v2000(name: str, tags: dict) -> str:
    """One SDF record: a two-atom connection table, then its property tags.

    The counts line is fixed width rather than whitespace separated, which is
    what the reader parses, so it is written the way a real writer writes it.
    """
    block = (
        f"{name}\n"
        "  Baker  01010000002D\n"
        "\n"
        "  2  1  0  0  0  0            999 V2000\n"
        "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "    1.5400    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "  1  2  1  0  0  0  0\n"
        "M  END\n"
    )
    for tag, value in tags.items():
        block += f"> <{tag}>\n{value}\n\n"
    return block + "$$$$\n"


#: Two molecules with the tags a compound library is queried on. The tags are
#: typed from their values across both records: an integer count, a decimal
#: mass, and a name that is neither.
LIGANDS_SDF = _v2000(
    "ethanol",
    {"COMPOUND_ID": "1", "MOLECULAR_WEIGHT": "46.07", "SOURCE": "in house"},
) + _v2000(
    "methanol",
    {"COMPOUND_ID": "2", "MOLECULAR_WEIGHT": "32.04", "SOURCE": "in house"},
)

#: A Tripos record, whose counts sit on the second line after the name.
ETHANOL_MOL2 = """\
@<TRIPOS>MOLECULE
ethanol
    3    2    1    0    0
SMALL
GASTEIGER

@<TRIPOS>ATOM
      1 C1     0.0000   0.0000   0.0000 C.3     1  ETH  -0.0600
      2 C2     1.5400   0.0000   0.0000 C.3     1  ETH   0.0400
      3 O1     2.0400   1.2000   0.0000 O.3     1  ETH  -0.3900
@<TRIPOS>BOND
     1    1    2 1
     2    2    3 1
"""

#: Little-endian and big-endian machine stamps, as MRC2014 writes them.
LITTLE_STAMP = b"\x44\x44\x00\x00"

#: Word 4. Mode 2 is float32, which is what a reconstruction is stored in.
MODE_FLOAT32 = 2

#: Bytes per voxel per mode, for the data block that follows the header.
_MODE_WIDTH = {0: 1, 1: 2, MODE_FLOAT32: 4, 6: 2, 12: 2}


def mrc_header(
    *,
    nx: int = 8,
    ny: int = 8,
    nz: int = 6,
    mode: int = MODE_FLOAT32,
    cell: tuple = (10.8, 10.8, 8.1),
    ispg: int = 1,
    label: str = "created by the croissant-baker test suite",
) -> bytes:
    """The 1024 bytes of an MRC2014 header, packed word by word.

    Sampling is the grid itself, so the voxel size the handler reports is the
    cell divided by the dimensions and nothing has to be kept in step by hand.
    """
    header = struct.pack("<4i", nx, ny, nz, mode)
    header += struct.pack("<3i", 0, 0, 0)
    header += struct.pack("<3i", nx, ny, nz)
    header += struct.pack("<3f", *cell)
    header += struct.pack("<3f", 90.0, 90.0, 90.0)
    header += struct.pack("<3i", 1, 2, 3)
    header += struct.pack("<3f", -1.5, 2.5, 0.25)
    header += struct.pack("<2i", ispg, 0)
    # Words 25 to 49 are the extra block; nversion is word 28, twelve bytes in.
    extra = bytearray(100)
    extra[12:16] = struct.pack("<i", 20140)
    header += bytes(extra)
    header += struct.pack("<3f", 0.0, 0.0, 0.0)
    header += b"MAP "
    header += LITTLE_STAMP
    header += struct.pack("<f", 0.75)
    header += struct.pack("<i", 1)
    labels = bytearray(b" " * 800)
    labels[: len(label)] = label.encode("ascii")
    header += bytes(labels)
    assert len(header) == 1024, len(header)
    return header


def mrc_bytes(**kwargs) -> bytes:
    """A whole MRC file: the header, then a data block of the size it declares.

    The voxels are zeros. The handler never reads them, but a file that stops
    at its header would be a truncated map rather than a small one.
    """
    header = mrc_header(**kwargs)
    nx, ny, nz, mode = struct.unpack("<4i", header[0:16])
    return header + bytes(nx * ny * nz * _MODE_WIDTH[mode])


def mtz_bytes(title: str = "native data", *, spacegroup: str = "P 21 21 21") -> bytes:
    """An MTZ as gemmi writes one: a base of H, K, L, then two measurements.

    Written through gemmi rather than packed by hand because the header offset
    it computes is the thing the reader relies on, and a hand-built file would
    only prove the reader agrees with this module. gemmi stamps no date, so the
    bytes are the same on every run.
    """
    import gemmi
    import numpy

    mtz = gemmi.Mtz(with_base=True)
    mtz.spacegroup = gemmi.find_spacegroup_by_name(spacegroup)
    mtz.set_cell_for_all(gemmi.UnitCell(40.0, 50.0, 60.0, 90.0, 90.0, 90.0))
    mtz.add_dataset("native")
    mtz.add_column("FP", "F")
    mtz.add_column("SIGFP", "Q")
    mtz.add_column("FREE", "I")
    rows = [[h, k, 0, 100.0 + h, 5.0, h % 20] for h in range(1, 9) for k in range(1, 4)]
    mtz.set_data(numpy.array(rows, dtype=numpy.float32))
    mtz.title = title

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "reflections.mtz"
        mtz.write_to_file(str(path))
        return path.read_bytes()


def mmcif_text(pdb_text: str = PDB_ENTRY, entry_id: str = "1ABC") -> str:
    """The mmCIF gemmi writes for a PDB entry, block named for the entry.

    A deposited file names its block for the entry rather than leaving the
    reader's placeholder, which is what ``entry_id`` restores here.
    """
    import gemmi

    st = gemmi.read_pdb_string(pdb_text)
    st.name = entry_id
    st.setup_entities()
    return st.make_mmcif_document().as_string()


def _write(path: Path, payload: bytes) -> Path:
    """One fixture file, written as the exact bytes given.

    Bytes rather than text: writing text translates newlines on some platforms,
    and a fixture whose bytes depend on the platform is not a fixture.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _write_gzip(path: Path, payload: bytes) -> Path:
    """The same, gzipped with no timestamp, so two runs write the same bytes.

    ``gzip.compress`` stamps the current time into the header by default, which
    would make every regeneration a diff.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(filename="", mode="wb", fileobj=path.open("wb"), mtime=0) as fh:
        fh.write(payload)
    return path


def write_demo(root: Path) -> Path:
    """Write the demo dataset under ``root``, one file per described path.

    The layout is what a structural biology deposit looks like when it is
    organised by experiment: entries beside the chemistry they contain, a
    cryo-EM processing directory with two jobs of the same name, and the
    reflections the X-ray entry was refined against. ``README.md`` is
    hand-written and not touched here, because it documents this function.
    """
    _write(root / "entries" / "1abc.pdb", PDB_ENTRY.encode("ascii"))
    _write(root / "entries" / "1abc.cif", mmcif_text().encode("ascii"))
    _write_gzip(
        root / "entries" / "model.cif.gz",
        mmcif_text(EM_ENTRY, "2EMD").encode("ascii"),
    )

    _write(root / "chemistry" / "glycine.cif", SMALL_MOLECULE_CIF.encode("ascii"))
    _write(root / "chemistry" / "ligands.sdf", LIGANDS_SDF.encode("ascii"))
    _write(root / "chemistry" / "ethanol.mol2", ETHANOL_MOL2.encode("ascii"))

    _write(root / "dictionaries" / "audit.cif", GENERIC_CIF.encode("ascii"))

    cryoem = root / "cryoem"
    _write(cryoem / "job001" / "run_data.star", PARTICLES_STAR.encode("ascii"))
    _write(cryoem / "job002" / "run_data.star", PARTICLES_STAR_SECOND.encode("ascii"))
    _write(cryoem / "postprocess.star", POSTPROCESS_STAR.encode("ascii"))
    _write(cryoem / "tomogram.mrc", mrc_bytes())
    # A stack of 2D images rather than a volume: ispg 0 is what says so.
    _write(cryoem / "particles.mrcs", mrc_bytes(nx=8, ny=8, nz=4, ispg=0))
    _write(cryoem / "tilt_series.mdoc", TILT_SERIES_MDOC.encode("ascii"))

    _write(root / "xray" / "native.mtz", mtz_bytes())
    return root
