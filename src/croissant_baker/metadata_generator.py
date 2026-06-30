"""Croissant metadata generator for datasets."""

import json
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import mlcroissant as mlc

from croissant_baker.files import discover_files
from croissant_baker.handlers.registry import find_handler, register_all_handlers

# Register all handlers
register_all_handlers()

# conformsTo URIs declared on the Dataset. mlcroissant defaults conforms_to to
# 1.0 even on 1.1.x — passing CROISSANT_CONFORMS_TO explicitly is the single
# source of truth for our declared spec version. RAI_CONFORMS_TO is appended
# to the conformsTo array by _ensure_rai_conforms_to() when RAI fields are
# present (the RAI extension vocab itself did NOT version-bump in Croissant 1.1).
# https://docs.mlcommons.org/croissant/docs/croissant-spec-1.1.html
CROISSANT_CONFORMS_TO = "http://mlcommons.org/croissant/1.1"
RAI_CONFORMS_TO = "http://mlcommons.org/croissant/RAI/1.0"


def serialize_datetime(obj):
    """Convert datetime objects to ISO format strings for JSON serialization."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _assert_unique_node_ids(distributions: list, record_sets: list) -> None:
    """Verify every emitted @id is unique across the document.

    JSON-LD merges nodes that share an @id (`json-ld11/#node-identifiers`
    spec section: nodes with the same identifier represent the same node).
    A collision therefore silently merges nodes, producing incorrect
    Croissant output. Surfacing the conflict here keeps the failure
    local to the generator with the offending @id and node types
    attached, instead of leaking out as an opaque downstream validation
    error or, worse, passing validation while silently dropping data.
    """
    seen: dict = {}

    def _claim(node_id, kind: str) -> None:
        if node_id is None:
            return
        if node_id in seen:
            raise ValueError(
                f"Croissant @id collision: '{node_id}' is used by both "
                f"{seen[node_id]} and {kind}. Every FileObject, FileSet, "
                f"RecordSet, and Field must carry a unique @id."
            )
        seen[node_id] = kind

    def _walk_fields(fields) -> None:
        for f in fields or []:
            _claim(getattr(f, "id", None), "Field")
            _walk_fields(getattr(f, "sub_fields", None))

    for d in distributions:
        _claim(getattr(d, "id", None), type(d).__name__)
    for r in record_sets:
        _claim(getattr(r, "id", None), "RecordSet")
        _walk_fields(getattr(r, "fields", None))


def _apply_field_mappings(
    metadata_dict: dict, mappings: Dict[str, Dict[str, object]]
) -> None:
    """Inject equivalentProperty / dataType overrides onto matching Fields.

    Walks the assembled metadata dict and applies user-supplied per-column
    overrides keyed by field name. Used to link columns to external
    vocabularies (e.g. Wikidata, SNOMED, LOINC). mlcroissant 1.1.0 exposes
    no Python parameter for ``equivalent_property``, so we patch the
    serialised JSON-LD directly.

    Matching is by bare field name across the entire metadata tree. A
    mapping for ``id`` will apply to every field named ``id`` in every
    RecordSet. When a name resolves to more than one field, a warning is
    printed so the user can confirm the override is intended for all of
    them.

    User-supplied ``data_types`` are APPENDED to the inferred Croissant type
    rather than replacing it. The mlcroissant validator requires at least
    one Croissant dataType per field, and the 1.1 spec explicitly supports
    multiple types coexisting (e.g. ``["sc:URL", "wd:Q515"]``).
    """
    match_counts: Dict[str, int] = defaultdict(int)

    def visit(node: object) -> None:
        if isinstance(node, dict):
            if node.get("@type") == "cr:Field":
                name = node.get("name")
                override = mappings.get(name)
                if override:
                    match_counts[name] += 1
                    if override.get("equivalent_property"):
                        node["equivalentProperty"] = override["equivalent_property"]
                    extra_types = override.get("data_types") or []
                    if extra_types:
                        existing = node.get("dataType")
                        if existing is None:
                            existing_list = []
                        elif isinstance(existing, list):
                            existing_list = list(existing)
                        else:
                            existing_list = [existing]
                        for t in extra_types:
                            if t not in existing_list:
                                existing_list.append(t)
                        node["dataType"] = existing_list
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(metadata_dict)

    for name, count in match_counts.items():
        if count > 1:
            print(
                f"Warning: field mapping '{name}' applied to {count} fields. "
                f"If '{name}' means different things in different RecordSets, "
                "rename the columns or split the bake."
            )


class MetadataGenerator:
    """
    Generates Croissant metadata for datasets with automatic type inference.

    Discovers files, delegates format-specific logic to registered handlers
    via the build_croissant protocol, and assembles the final JSON-LD.
    """

    def __init__(
        self,
        dataset_path: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        url: Optional[str] = None,
        license: Optional[str] = None,
        citation: Optional[str] = None,
        version: Optional[str] = None,
        date_published: Optional[str] = None,
        date_created: Optional[str] = None,
        date_modified: Optional[str] = None,
        creators: Optional[List[Dict[str, str]]] = None,
        publisher: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        in_language: Optional[List[str]] = None,
        same_as: Optional[List[str]] = None,
        sd_license: Optional[str] = None,
        sd_version: Optional[str] = None,
        alternate_name: Optional[str] = None,
        is_live_dataset: Optional[bool] = None,
        temporal_coverage: Optional[str] = None,
        usage_info: Optional[str] = None,
        field_mappings: Optional[Dict[str, Dict[str, object]]] = None,
        count_csv_rows: bool = False,
        includes: Optional[List[str]] = None,
        excludes: Optional[List[str]] = None,
        rai_fields: Optional[Dict[str, object]] = None,
    ):
        """
        Initialize the metadata generator for a dataset.

        Args:
            dataset_path: Path to the directory containing dataset files.
            name: Dataset name (defaults to directory name).
            description: Dataset description.
            url: Dataset URL.
            license: License URL or SPDX identifier (e.g. "CC-BY-4.0").
            citation: Citation text, preferably BibTeX format.
            version: Dataset version string.
            date_published: Publication date in ISO format ("2023-12-15" or
                "2023-12-15T10:30:00").
            date_created: Creation date in ISO format.
            date_modified: Last-modified date in ISO format.
            creators: List of dicts with "name", "email", and/or "url" keys.
            publisher: Name of the publishing organization (schema.org/Organization).
            keywords: Topical keywords for dataset discovery (schema.org/keywords).
            in_language: BCP 47 language code(s) (e.g. "en"). Multiple supported.
            same_as: URLs of equivalent dataset records (e.g. DOI, mirror landing
                pages). Multiple values supported per schema.org/sameAs.
            sd_license: License of the metadata description itself, distinct from
                the data license (schema.org/sdLicense).
            sd_version: Version of the metadata description, distinct from
                ``version``. Defaults to None — only emitted when set.
            alternate_name: Short alias for the dataset (schema.org/alternateName).
            is_live_dataset: Mark dataset as a live, evolving stream.
            temporal_coverage: Time period the data covers — schema.org accepts
                free text or ISO 8601 (e.g., "2008/2019", "2023-01-15").
            usage_info: URL of a usage/consent policy (e.g., a DUO term URL,
                ODRL Offer URL).
            field_mappings: Per-column overrides keyed by field name. Each value
                is a dict with optional ``equivalent_property`` (vocab URI) and
                ``data_types`` (list of vocab URIs). Used to link columns to
                external vocabularies like Wikidata/SNOMED/LOINC.
            count_csv_rows: If True, scan each CSV fully for exact row counts.
                Defaults to False for performance.
            includes: Glob patterns to include. Applied before excludes.
            excludes: Glob patterns to exclude. Applied after includes.
            rai_fields: Native mlcroissant RAI metadata fields, passed through
                to ``mlc.Metadata`` unchanged.

        Raises:
            ValueError: If dataset_path is not a directory.
        """
        self.dataset_path = Path(dataset_path).resolve()
        if not self.dataset_path.is_dir():
            raise ValueError(f"Dataset path {dataset_path} is not a directory")

        self.name = name
        self.description = description
        self.url = url
        self.license = license
        self.citation = citation
        self.version = version
        self.date_published = date_published
        self.date_created = date_created
        self.date_modified = date_modified
        self.creators = creators
        self.publisher = publisher
        self.keywords = keywords
        self.in_language = in_language
        self.same_as = same_as
        self.sd_license = sd_license
        self.sd_version = sd_version
        self.alternate_name = alternate_name
        self.is_live_dataset = is_live_dataset
        self.temporal_coverage = temporal_coverage
        self.usage_info = usage_info
        self.field_mappings = field_mappings or {}
        self.includes = includes
        self.excludes = excludes
        self.rai_fields = rai_fields or {}
        # Generic options forwarded to every handler via **kwargs.
        # Handlers declare what they use; others ignore the rest.
        # To add a new handler-specific flag: add one key here — the call site never changes.
        self._handler_kwargs = {
            "count_rows": count_csv_rows,
        }

    def generate_metadata(self, progress_callback=None) -> dict:
        """Generate complete Croissant metadata for the dataset.

        Args:
            progress_callback: Optional callback with signature
                (current: int, total: int, file_path: str) -> None
                called before processing each file.
        """
        files = discover_files(
            str(self.dataset_path),
            include_patterns=self.includes,
            exclude_patterns=self.excludes,
        )

        # Extract metadata as (handler, meta) pairs so handler identity is
        # stored by reference, not by id() — no fragility if dicts are copied.
        total_files = len(files)
        file_metadata: list[tuple] = []
        # Track files that look like a recognised binary format by extension
        # but were rejected at handler-selection time (e.g. .dcm files
        # without the DICM preamble at offset 128). These are valid skips,
        # not errors, but worth surfacing so the user knows not all .dcm
        # files made it into the output.
        unmatched_by_ext: dict[str, int] = {}
        for i, file_path in enumerate(files):
            if progress_callback:
                progress_callback(i, total_files, str(file_path))
            full_path = self.dataset_path / file_path
            handler = find_handler(full_path)
            if handler:
                try:
                    meta = handler.extract_metadata(full_path, **self._handler_kwargs)
                    meta["relative_path"] = str(file_path)
                    file_metadata.append((handler, meta))
                except Exception as e:
                    print(f"Warning: Failed to process {file_path}: {e}")
            else:
                ext = full_path.suffix.lower()
                if ext in {".dcm", ".dicom"}:
                    unmatched_by_ext[ext] = unmatched_by_ext.get(ext, 0) + 1

        if unmatched_by_ext:
            total = sum(unmatched_by_ext.values())
            print(
                f"Note: skipped {total} DICOM file(s) without the DICM preamble "
                "(offset 128). These are typically DICOMDIR fragments or non-"
                "standalone DICOM exports."
            )

        if not file_metadata:
            raise ValueError("No supported files found in the dataset")

        metadata = mlc.Metadata(
            name=self.name or self.dataset_path.name,
            description=self._build_description(file_metadata),
            url=self.url,
            license=self._resolve_license(),
            creators=self._build_creators(),
            date_published=self._resolve_date(),
            date_created=self._parse_iso(self.date_created),
            date_modified=self._parse_iso(self.date_modified),
            version=self.version or "1.0.0",
            cite_as=self._build_citation(),
            conforms_to=CROISSANT_CONFORMS_TO,
            keywords=self.keywords,
            in_language=self.in_language,
            same_as=self.same_as,
            publisher=self._build_publisher(),
            sd_licence=self.sd_license,
            **self.rai_fields,
        )

        # distributions holds both FileObjects and FileSets — the full contents
        # of the Croissant `distribution` array per the spec.
        distributions = []
        record_sets = []
        # Use a counter (not enumerate) for unique FileObject IDs: some formats
        # (e.g. WFDB) create multiple FileObjects per meta via related_files,
        # so enumerate would produce ID collisions.
        file_counter = 0
        _batch_handlers: dict = defaultdict(list)

        for handler, file_meta in file_metadata:
            file_id = f"file_{file_counter}"
            file_counter += 1

            distributions.append(
                mlc.FileObject(
                    id=file_id,
                    name=file_meta["file_name"],
                    content_url=file_meta["relative_path"],
                    encoding_formats=[file_meta["encoding_format"]],
                    content_size=str(file_meta["file_size"]),
                    sha256=file_meta["sha256"],
                )
            )

            # Multi-file records (e.g. WFDB: .hea + .dat + .atr): the generator
            # owns FileObject creation for every physical file. RecordSet
            # construction is delegated to the handler via build_croissant.
            if "related_files" in file_meta:
                for related in file_meta["related_files"]:
                    related_id = f"file_{file_counter}"
                    file_counter += 1
                    rel_path = Path(related["path"])
                    distributions.append(
                        mlc.FileObject(
                            id=related_id,
                            name=related["name"],
                            content_url=str(rel_path.relative_to(self.dataset_path)),
                            encoding_formats=[related["encoding"]],
                            content_size=str(related["size"]),
                            sha256=related["sha256"],
                        )
                    )

            _batch_handlers[handler].append((file_id, file_meta))

        # Each handler builds its FileSets + RecordSets and returns them.
        # Handlers never return FileObjects — those are owned by the generator.
        # TODO: future improvements per handler:
        #   - references: detect foreign-key columns (e.g. subject_id) and emit
        #     cr:references links between RecordSets — high-impact for EHR data.
        #   - enumerations: for low-cardinality categorical columns, emit
        #     sc:Enumeration RecordSets.
        for _h, pairs in _batch_handlers.items():
            try:
                filesets, rs = _h.build_croissant(
                    [m for _, m in pairs],
                    [fid for fid, _ in pairs],
                )
                distributions.extend(filesets)
                record_sets.extend(rs)
            except Exception as e:
                print(f"Warning: {type(_h).__name__}.build_croissant failed: {e}")

        _assert_unique_node_ids(distributions, record_sets)

        metadata.distribution = distributions
        metadata.record_sets = record_sets

        result = metadata.to_json()
        # Spec fields without a native mlcroissant parameter — inject
        # post-serialisation. Keep keys absent (not null) when the caller
        # didn't supply a value, so optional fields don't pollute outputs
        # that don't need them. ``sd_version`` IS a native mlc 1.1.0 param,
        # but mlc emits it as ``cr:sdVersion`` (no @context alias); the
        # canonical 1.1 examples use the unprefixed form, so we write the
        # canonical key directly.
        if self.sd_version is not None:
            result["sdVersion"] = self.sd_version
        if self.alternate_name is not None:
            result["alternateName"] = self.alternate_name
        if self.is_live_dataset is not None:
            result["isLiveDataset"] = self.is_live_dataset
        if self.temporal_coverage is not None:
            result["temporalCoverage"] = self.temporal_coverage
        if self.usage_info is not None:
            result["usageInfo"] = self.usage_info
        if self.field_mappings:
            _apply_field_mappings(result, self.field_mappings)
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_description(self, file_metadata: list) -> str:
        if self.description:
            return self.description
        file_types = {m.get("encoding_format", "unknown") for _, m in file_metadata}
        return (
            f"Dataset containing {len(file_metadata)} files "
            f"({', '.join(sorted(file_types))}) with automatically inferred types and structure"
        )

    def _resolve_license(self) -> str:
        if not self.license:
            return "https://creativecommons.org/licenses/by/4.0/"
        if self.license.startswith(("http://", "https://")):
            return self.license
        spdx_to_url = {
            "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
            "CC-BY-SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
            "CC-BY-NC-4.0": "https://creativecommons.org/licenses/by-nc/4.0/",
            "CC-BY-ND-4.0": "https://creativecommons.org/licenses/by-nd/4.0/",
            "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
            "MIT": "https://opensource.org/licenses/MIT",
            "Apache-2.0": "https://www.apache.org/licenses/LICENSE-2.0",
            "GPL-3.0": "https://www.gnu.org/licenses/gpl-3.0.html",
            "BSD-3-Clause": "https://opensource.org/licenses/BSD-3-Clause",
        }
        return spdx_to_url.get(self.license, self.license)

    def _build_creators(self) -> list:
        if not self.creators:
            return [mlc.Person(name="Dataset Creator", email="creator@example.com")]
        return [
            mlc.Person(**{k: v for k, v in c.items() if k in ("name", "email", "url")})
            for c in self.creators
        ]

    def _build_publisher(self):
        if not self.publisher:
            return None
        return [mlc.Organization(name=self.publisher)]

    @staticmethod
    def _parse_iso(value: Optional[str]):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError as e:
            raise ValueError(
                f"Invalid ISO date: '{value}'. "
                f"Expected '2023-12-15' or '2023-12-15T10:30:00'. Error: {e}"
            )

    def _build_citation(self) -> Optional[str]:
        """Build the default citation, or None.

        A caller-supplied ``citation`` is used verbatim. Otherwise it is derived
        only from real metadata — the supplied creators and the year of the
        publication/creation date — so the same input always bakes to the same
        citation (no wall-clock state). With neither a creator nor a date there
        is nothing real to cite, so ``cite_as`` is omitted rather than invented.
        """
        if self.citation:
            return self.citation

        names = [
            c["name"]
            for c in (self.creators or [])
            if isinstance(c, dict) and c.get("name")
        ]
        author = ", ".join(names) if names else None

        year = None
        for raw in (self.date_published, self.date_created):
            if not raw:
                continue
            try:
                year = datetime.fromisoformat(raw).year
                break
            except ValueError:
                continue

        if author is None and year is None:
            return None

        name = self.name or self.dataset_path.name
        author_part = author if author else "Unknown"
        year_part = f" ({year})." if year is not None else ""
        return f"{author_part}.{year_part} {name} [Data set]."

    def _resolve_date(self) -> Optional[datetime]:
        """Return the parsed publication date, or None when none was supplied.

        ``datePublished`` is omitted when unset rather than defaulted, so output
        stays reproducible (no generation-time wall clock leaks into it) and we
        don't assert a publication date the caller never gave. It is optional in
        schema.org/Croissant.
        """
        if not self.date_published:
            return None
        try:
            return datetime.fromisoformat(self.date_published)
        except ValueError as e:
            raise ValueError(
                f"Invalid date format for --date-published: '{self.date_published}'. "
                f"Expected ISO format like '2023-12-15' or '2023-12-15T10:30:00'. Error: {e}"
            )

    def save_metadata(self, output_path: str, validate: bool = True) -> None:
        """Generate and save Croissant metadata to a file.

        Args:
            output_path: Path where the JSON-LD metadata file will be written.
            validate: If True (default), validates with mlcroissant before saving.

        Raises:
            ValueError: If validation fails or the file cannot be saved.
        """
        metadata_dict = self.generate_metadata()
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        if validate:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".jsonld", delete=False
            ) as tmp_file:
                json.dump(
                    metadata_dict,
                    tmp_file,
                    indent=2,
                    ensure_ascii=False,
                    default=serialize_datetime,
                )
                tmp_path = tmp_file.name
            try:
                mlc.Dataset(tmp_path)
                self._save_to_file(metadata_dict, output_file)
            except Exception as e:
                raise ValueError(f"Validation failed: {e}")
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        else:
            self._save_to_file(metadata_dict, output_file)

    def _save_to_file(self, metadata_dict: dict, output_file: Path) -> None:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(
                metadata_dict,
                f,
                indent=2,
                ensure_ascii=False,
                default=serialize_datetime,
            )
            f.write("\n")
