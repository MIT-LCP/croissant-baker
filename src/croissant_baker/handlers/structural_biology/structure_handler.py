"""Macromolecular structures: PDB, mmCIF, and the small-molecule CIF beside them.

Every format here is parsed by gemmi, which is an optional extra, so the import
is guarded and a file is refused with an install hint when it is missing.
"""

from __future__ import annotations

import logging
from pathlib import Path

try:
    import gemmi
except ImportError:
    # The handler stays registered and keeps claiming its suffixes, so a
    # ``.pdb`` is reported as a file this install cannot read rather than as
    # one nothing recognises.
    gemmi = None

import mlcroissant as mlc

from croissant_baker.handlers.base_handler import BuildResult, FileTypeHandler
from croissant_baker.handlers.structural_biology import cif, tables
from croissant_baker.handlers.utils import display_name
from croissant_baker.sources import FileSource

logger = logging.getLogger(__name__)

#: One FileSet over the batch and one record per file in it.
FILE_SET_ID = "structure-files"
RECORD_SET_ID = "structures"

#: The key an extracted structure file carries its header under. A CIF holding
#: no structure carries :data:`~...tables.TABLES` instead, and which key a file
#: has is what decides how it is described.
STRUCTURE = "structure_properties"

#: All three unregistered: no IANA type exists for any of these formats, and
#: these are the spellings the chemistry tools have used for decades. They
#: document the file rather than make a record set readable, since mlcroissant
#: has no reader for structure files at any media type.
PDB_MIME_TYPE = "chemical/x-pdb"
MMCIF_MIME_TYPE = "chemical/x-mmcif"
#: A CIF document that describes no structure: a dictionary, a deposition log,
#: a validation report. It is CIF, and claiming mmCIF would claim an entry.
CIF_MIME_TYPE = "chemical/x-cif"

#: The suffixes that carry PDB records rather than CIF syntax.
_PDB_SUFFIXES = (".pdb", ".ent")


def _decode(data: bytes) -> str:
    """The file as text. Both formats are line-oriented ASCII by definition, so
    a byte that is not is damage, and replacing it lets the reader report the
    damage rather than the decoder."""
    return data.decode("utf-8", errors="replace")


def header_value(st: gemmi.Structure, tag: str) -> str:
    """One header value, or the empty string. ``gemmi.InfoMap`` is not a dict:
    it supports membership and lookup and nothing else."""
    return st.info[tag] if tag in st.info else ""


def read_pdb(text: str) -> gemmi.Structure:
    """The structure a PDB file holds.

    gemmi accepts anything here: a file of prose parses into a structure with
    no atom sites rather than raising, so emptiness is the error to report.
    """
    st = gemmi.read_pdb_string(text)
    if not len(st) or not st[0].count_atom_sites():
        raise ValueError("no ATOM or HETATM records, so this is not a structure")
    return st


def has_tag(block: gemmi.cif.Block, tag: str) -> bool:
    """Whether the block carries this tag, as a lone value or as a loop column."""
    return block.find_value(tag) is not None or bool(block.find_loop(tag))


def read_cif(text: str) -> dict:
    """What a CIF document contributes to the description, and under which key.

    One suffix, ``.cif``, covers three unrelated dictionaries: an mmCIF entry
    writes dotted category tags such as ``_atom_site.Cartn_x``, a
    small-molecule CIF writes the underscore-only tags of the core dictionary,
    ``_atom_site_fract_x``, and a dictionary or a deposition log carries no
    atoms at all. The tags are the only thing that tells the first two apart,
    and the third is what is left.

    Only the first block is asked about a structure, not the sole one: a
    deposited entry is often followed by the chemical component blocks its
    ligands were taken from. The tables, when it comes to those, are every
    block's, because there is no entry to make the first one special.
    """
    doc = gemmi.cif.read_string(text)
    if not len(doc):
        raise ValueError("the CIF document holds no data block")
    block = doc[0]

    if block.find_mmcif_category("_atom_site.") or has_tag(block, "_entry.id"):
        st = gemmi.make_structure_from_block(block)
        if len(st) and st[0].count_atom_sites():
            # The block name is the entry id in a deposited file, and unlike the
            # PDB reader's placeholder it is the file's own.
            return {
                STRUCTURE: macromolecular_properties(
                    st, "mmCIF", header_value(st, "_entry.id") or st.name
                )
            }
        # A validation report names the entry it reports on, and a stripped
        # deposition keeps the atom site columns and drops their rows. Neither
        # builds a model, and counting chains in one that does not exist raises
        # where describing the columns says what the file actually holds.

    if has_tag(block, "_cell_length_a") or has_tag(block, "_atom_site_label"):
        return {
            STRUCTURE: small_molecule_properties(
                gemmi.make_small_structure_from_block(block), block
            )
        }

    described = cif.describe(doc)
    if not described:
        raise ValueError(
            f"data block '{block.name}' is neither a structure nor a table: it "
            "declares no atom site a structure could be built from, and no tag "
            "a column could be named after"
        )
    return {tables.TABLES: described}


def macromolecular_properties(st: gemmi.Structure, fmt: str, entry_id: str) -> dict:
    """What a deposited entry says about itself, and what it contains.

    Only the first model is counted: the models of an NMR ensemble hold the
    same chains and residues, so summing them would report the ensemble size
    twice.
    """
    model = st[0]
    props = {
        "kind": "macromolecular",
        "format": fmt,
        "entry_id": entry_id,
        "title": header_value(st, "_struct.title"),
        "experimental_method": header_value(st, "_exptl.method"),
        "n_models": len(st),
        "n_chains": len(model),
        "n_residues": sum(len(chain) for chain in model),
        "n_atoms": model.count_atom_sites(),
    }
    # Each of these is absent rather than reported empty: zero is gemmi's "not
    # stated" and not a measured resolution, and a structure solved outside a
    # crystal carries a placeholder cell that would read as a real one.
    if st.resolution:
        props["resolution_angstrom"] = float(st.resolution)
    if st.spacegroup_hm:
        props["space_group"] = st.spacegroup_hm
    if st.cell.is_crystal():
        props["unit_cell"] = _cell(st.cell)
    return props


def _cell(cell: gemmi.UnitCell) -> tuple:
    """The six cell parameters, in the order crystallography states them."""
    return (
        float(cell.a),
        float(cell.b),
        float(cell.c),
        float(cell.alpha),
        float(cell.beta),
        float(cell.gamma),
    )


def small_molecule_properties(
    small: gemmi.SmallStructure, block: gemmi.cif.Block
) -> dict:
    """What a core-dictionary CIF describes: one molecule in a unit cell.

    The block is still needed alongside the parsed structure, because
    ``gemmi.SmallStructure`` carries no formula: the sum formula lives only in
    the tag it was written under.
    """
    props = {
        "kind": "small molecule",
        "format": "CIF",
        "entry_id": small.name,
        "n_atoms": len(small.sites),
    }
    formula = block.find_value("_chemical_formula_sum")
    if formula is not None:
        # Quoting is CIF syntax rather than part of the value.
        props["formula"] = gemmi.cif.as_string(formula)
    if small.spacegroup_hm:
        props["space_group"] = small.spacegroup_hm
    if small.cell.is_crystal():
        props["unit_cell"] = _cell(small.cell)
    return props


class StructureHandler(FileTypeHandler):
    """Handler for macromolecular structures and the CIF files beside them.

    A PDB entry, an mmCIF entry and a small-molecule CIF are all structures and
    share one FileSet and one record set, because they answer the same
    questions. A ``.cif`` that carries no structure at all, a dictionary or a
    deposition log, is described as the tables it declares instead, the way a
    STAR file is.

    The per-file fields read ``fileProperty: content`` over the FileSet, the
    way the NIfTI and DICOM fields do, because that is what the record set is:
    one record per file, not one per atom. Reading that content selects no
    header attribute, and mlcroissant dispatches its reader on
    ``encodingFormat`` over a fixed list none of these formats is on, so the
    fields describe the batch rather than promise a read.
    """

    EXTENSIONS = (".pdb", ".ent", ".cif", ".mmcif")
    FORMAT_NAME = "Macromolecular structure"
    FORMAT_DESCRIPTION = (
        "Entry id, title, experimental method, resolution, unit cell, space "
        "group, and model, chain, residue and atom counts; a CIF holding no "
        "structure is described as the tables it declares"
    )

    def claims(self, source: FileSource) -> bool:
        return source.suffix in self.EXTENSIONS

    def extract(self, source: FileSource, **kwargs) -> dict:
        cif.require_gemmi(gemmi, "PDB, mmCIF and CIF")
        if not source.exists:
            raise FileNotFoundError(f"Structure file not found: {source.relative_path}")

        is_pdb = source.suffix in _PDB_SUFFIXES
        try:
            with source.open() as stream:
                text = _decode(stream.read())
            if is_pdb:
                st = read_pdb(text)
                # From the HEADER record, which gemmi files under the mmCIF tag
                # it maps to. Never ``st.name``: reading from a string leaves
                # that as the reader's own placeholder.
                described = {
                    STRUCTURE: macromolecular_properties(
                        st, "PDB", header_value(st, "_entry.id")
                    )
                }
            else:
                described = read_cif(text)
        except Exception as e:
            raise ValueError(
                f"Failed to read structure file {source.relative_path}: {e}"
            ) from e

        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": _encoding_format(is_pdb, described),
            **described,
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        """One FileSet and one record set over the structures, and one record
        set per table for each CIF document that holds no structure.

        Every structure file answers the same questions, so they share a
        schema; splitting them per file would repeat that schema once per entry
        and say nothing extra. A CIF document answers none of those questions,
        so it is described the way a STAR file is, as the tables it declares.
        """
        # An empty batch has nothing to summarise; a FileSet over zero files
        # would describe data that is not there.
        if not file_metas:
            return BuildResult([], [])

        structures = _select(file_metas, file_ids, STRUCTURE)
        documents = _select(file_metas, file_ids, tables.TABLES)

        file_sets, record_sets = [], []
        if structures:
            metas = [meta for meta, _ in structures]
            summary = collect_structure_summary(metas)
            file_sets.append(
                mlc.FileSet(
                    id=FILE_SET_ID,
                    name="Structure files",
                    description=_file_set_description(summary),
                    encoding_formats=sorted({m["encoding_format"] for m in metas}),
                    includes=[f"**/*{suffix}" for suffix in summary["suffixes"]],
                    # A ``**/*.cif`` reaches the documents too, and the record
                    # set reading this FileSet counts one record per structure
                    # file. Naming them keeps them out of that count.
                    excludes=sorted(_relative(meta) for meta, _ in documents) or None,
                )
            )
            record_sets.append(
                mlc.RecordSet(
                    id=RECORD_SET_ID,
                    name=RECORD_SET_ID,
                    description=_record_set_description(summary),
                    fields=_fields(summary),
                )
            )
        if documents:
            record_sets.extend(
                tables.record_sets(
                    [meta for meta, _ in documents],
                    [file_id for _, file_id in documents],
                )
            )
        return BuildResult(file_sets, record_sets)


def _encoding_format(is_pdb: bool, described: dict) -> str:
    """What the file is, which for a ``.cif`` is not settled by its suffix.

    Only a macromolecular entry is mmCIF. A small-molecule crystal and a
    document that holds no structure are both written in another dictionary of
    the same syntax, so calling either one mmCIF would misname it.
    """
    if is_pdb:
        return PDB_MIME_TYPE
    props = described.get(STRUCTURE)
    return MMCIF_MIME_TYPE if props and props["format"] == "mmCIF" else CIF_MIME_TYPE


def _relative(meta: dict) -> str:
    """The file's logical dataset-relative path, which a FileSet resolves.

    Rendered with forward slashes rather than the host's separator, because
    what it goes into is a glob: a path assembled on Windows would carry
    backslashes a reader then has to match literally.
    """
    return Path(meta.get("relative_path", meta["file_name"])).as_posix()


def _select(file_metas: list, file_ids: list, key: str) -> list:
    """The ``(meta, file id)`` pairs of the batch carrying ``key``.

    Paired rather than filtered twice: the identifier belongs to its own file,
    and a mixed batch is where taking it by position would go wrong.
    """
    return [(meta, id_) for meta, id_ in zip(file_metas, file_ids) if key in meta]


# Describing a batch


def collect_structure_summary(file_metas: list) -> dict:
    """What the whole batch has in common, for the descriptions and the fields.

    Ordered rather than set-shaped wherever it reaches a description, so a
    manifest does not change between two bakes of the same directory.
    """
    props = [meta.get(STRUCTURE, {}) for meta in file_metas]
    suffixes = {Path(meta["file_name"]).suffix.lower() for meta in file_metas}
    resolutions = [
        p["resolution_angstrom"] for p in props if p.get("resolution_angstrom")
    ]
    methods = sorted(
        {p["experimental_method"] for p in props if p.get("experimental_method")}
    )

    summary = {
        "num_files": len(file_metas),
        # In declared order, not discovery order: the glob list is part of the
        # manifest and should not depend on which file the scan reached first.
        "suffixes": [s for s in StructureHandler.EXTENSIONS if s in suffixes],
        "formats": sorted({p["format"] for p in props if p.get("format")}),
        "methods": methods,
        "has_formula": any("formula" in p for p in props),
        "stored_name": display_name(file_metas[0]) if len(file_metas) == 1 else "",
    }
    if resolutions:
        summary["resolution_range"] = (min(resolutions), max(resolutions))
    return summary


def _named(summary: dict) -> str:
    """How prose refers to the batch: the one file by name, or a count."""
    files = f"{summary['num_files']} structure file(s)"
    return f"{files}, {summary['stored_name']}" if summary["stored_name"] else files


def _file_set_description(summary: dict) -> str:
    return f"{_named(summary)} in {', '.join(summary['formats']) or 'CIF'} format"


def _record_set_description(summary: dict) -> str:
    parts = [
        f"One record per structure file: {_named(summary)} in "
        f"{', '.join(summary['formats']) or 'CIF'} format."
    ]
    if summary["methods"]:
        parts.append(f"Experimental method(s): {', '.join(summary['methods'])}.")
    span = summary.get("resolution_range")
    if span:
        low, high = span
        stated = f"{low:g} A" if low == high else f"{low:g} to {high:g} A"
        parts.append(f"Resolution: {stated}.")
    return " ".join(parts)


# Fields

#: Every field, in the order a reader meets them: what the entry calls itself,
#: how it was measured, where it sits in a cell, then how big it is. Each entry
#: is (name, Croissant type, description).
_FIELDS = (
    ("entry_id", "sc:Text", "_entry.id, or the identifier on the HEADER record"),
    ("title", "sc:Text", "_struct.title, or the TITLE record of a PDB file"),
    (
        "experimental_method",
        "sc:Text",
        "_exptl.method, or the EXPDTA record of a PDB file",
    ),
    (
        "resolution_angstrom",
        "sc:Float",
        "_refine.ls_d_res_high, or the RESOLUTION value of REMARK 2",
    ),
    (
        "space_group",
        "sc:Text",
        "_symmetry.space_group_name_H-M, or the group named on CRYST1",
    ),
    (
        "unit_cell",
        "sc:Text",
        "_cell lengths a, b, c and angles alpha, beta, gamma, or the same six "
        "values from CRYST1",
    ),
    ("n_models", "sc:Integer", "Models in the file, an NMR ensemble's members"),
    ("n_chains", "sc:Integer", "Chains in the first model"),
    ("n_residues", "sc:Integer", "Residues across the chains of the first model"),
    ("n_atoms", "sc:Integer", "Rows of _atom_site, or ATOM and HETATM records"),
    (
        "formula",
        "sc:Text",
        "_chemical_formula_sum, the sum formula of a small-molecule CIF",
    ),
    ("format", "sc:Text", "PDB, mmCIF or CIF, from the syntax the file is written in"),
)

#: Fields only some batches can fill. Emitting one nothing fills would promise
#: a column empty in every row.
_CONDITIONAL = {
    "resolution_angstrom": lambda summary: "resolution_range" in summary,
    "formula": lambda summary: summary["has_formula"],
}


def _fields(summary: dict) -> list:
    return [
        mlc.Field(
            id=f"{RECORD_SET_ID}/{name}",
            name=name,
            description=description,
            data_types=[data_type],
            source=mlc.Source(
                file_set=FILE_SET_ID,
                extract=mlc.Extract(file_property="content"),
            ),
        )
        for name, data_type, description in _FIELDS
        if _CONDITIONAL.get(name, lambda _: True)(summary)
    ]
