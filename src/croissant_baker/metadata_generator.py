"""Croissant metadata generator for datasets."""

import json
import logging
import os
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import mlcroissant as mlc

from croissant_baker.assembly import (
    _encoding_formats,
    _resolve_file_sets,
)
from croissant_baker.identifiers import (
    _assert_unique_node_ids,
    _disambiguate_record_sets,
    serialize_datetime,
)

from croissant_baker import compression
from croissant_baker.handlers.base_handler import BuildResult
from croissant_baker.handlers.registry import (
    HandlerRegistry,
    default_registry,
    extract as extract_with,
)
from croissant_baker.scan import (
    Outcome,
    Reason,
    ScanEntry,
    ScanReport,
    resolve_duplicates,
    scan_directory,
)

logger = logging.getLogger(__name__)

_CARRIED_BY_ANOTHER = (Outcome.UNCLAIMED, Outcome.FAILED)

# conformsTo URIs declared on the Dataset. mlcroissant defaults conforms_to to
# 1.0 even on 1.1.x — passing CROISSANT_CONFORMS_TO explicitly is the single
# source of truth for our declared spec version. RAI_CONFORMS_TO is appended
# to the conformsTo array by _ensure_rai_conforms_to() when RAI fields are
# present (the RAI extension vocab itself did NOT version-bump in Croissant 1.1).
# https://docs.mlcommons.org/croissant/docs/croissant-spec-1.1.html
CROISSANT_CONFORMS_TO = "http://mlcommons.org/croissant/1.1"
RAI_CONFORMS_TO = "http://mlcommons.org/croissant/RAI/1.0"


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
            logger.warning(
                "field mapping '%s' applied to %d fields. If '%s' means "
                "different things in different RecordSets, rename the columns "
                "or split the bake.",
                name,
                count,
                name,
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
        max_workers: Optional[int] = None,
        includes: Optional[List[str]] = None,
        excludes: Optional[List[str]] = None,
        rai_fields: Optional[Dict[str, object]] = None,
        handlers: Optional[HandlerRegistry] = None,
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
            max_workers: Maximum worker threads for per-file metadata
                extraction. None (default) auto-sizes from the CPU count; 1
                forces serial. Output is identical regardless of this value.
            includes: Glob patterns to include. Applied before excludes.
            excludes: Glob patterns to exclude. Applied after includes.
            rai_fields: Native mlcroissant RAI metadata fields, passed through
                to ``mlc.Metadata`` unchanged.
            handlers: Which handlers to consult, and in what order. Defaults to
                the built-in registry. Supply one to bake with a narrower set,
                or with a handler the baker does not ship.

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
        self.max_workers = max_workers
        self.handlers = handlers if handlers is not None else default_registry()
        # Generic options forwarded to every handler via **kwargs.
        # Handlers declare what they use; others ignore the rest.
        # To add a new handler-specific flag: add one key here — the call site never changes.
        self._handler_kwargs = {
            "count_rows": count_csv_rows,
        }
        # One entry per file the last generate_metadata() call scanned, each
        # carrying what became of it. Empty until then.
        self._scan_entries: list[ScanEntry] = []

    @property
    def scan_report(self) -> ScanReport:
        """Coverage of the last ``generate_metadata()`` call.

        One entry per file the scan found, each carrying its outcome and, where
        it was not described, the reason. Populated before the "No supported
        files found in the dataset" guard fires, so a caller catching that
        error can still ask why. Empty before the first call.
        """
        return ScanReport(self._scan_entries)

    def generate_metadata(self, progress_callback=None) -> dict:
        """Generate complete Croissant metadata for the dataset.

        Per-file metadata extraction (handler selection, whole-file SHA-256,
        header/schema reads) is I/O-bound and independent across files, so it
        runs on a thread pool sized by ``max_workers``. Results are reassembled
        in discovery order before any FileObject @id is assigned, so the
        document is identical regardless of worker count. Nothing is reported
        per file as it goes: what became of each one is in
        :attr:`scan_report`, which the CLI prints under ``--verbose``.

        Args:
            progress_callback: Optional callback with signature
                (completed: int, total: int, file_path: str) -> None
                invoked once per file as it finishes extraction.
        """
        entries = scan_directory(
            str(self.dataset_path),
            include_patterns=self.includes,
            exclude_patterns=self.excludes,
        )
        self._scan_entries = entries
        total_files = len(entries)

        # Each worker touches only its own entry, and entries are read back in
        # scan order below, so assembly stays deterministic whatever order the
        # threads finish in.
        workers = self._resolve_worker_count(total_files)
        if workers == 1:
            for i, entry in enumerate(entries):
                self._extract_entry(entry)
                if progress_callback:
                    progress_callback(i + 1, total_files, str(entry.path))
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_to_entry = {
                    pool.submit(self._extract_entry, e): e for e in entries
                }
                completed = 0
                for future in as_completed(future_to_entry):
                    future.result()
                    completed += 1
                    if progress_callback:
                        progress_callback(
                            completed, total_files, str(future_to_entry[future].path)
                        )

        # Before assembly, so a duplicate is linked rather than colliding on an
        # @id; after extraction, so a file whose duplicate failed to parse is
        # still described.
        resolve_duplicates(entries, self.dataset_path)

        # Read back in scan order.
        ready: list[ScanEntry] = []
        linked: list[ScanEntry] = []
        # Files that look like a recognised binary format by extension but were
        # rejected at handler-selection time (e.g. .dcm files without the DICM
        # preamble at offset 128) are valid skips, not errors — surfaced so the
        # user knows not all such files made it into the output.
        unmatched_by_ext: dict[str, int] = {}
        for entry in entries:
            if entry.outcome is Outcome.UNCLAIMED:
                ext = (self.dataset_path / entry.path).suffix.lower()
                if ext in {".dcm", ".dicom"}:
                    unmatched_by_ext[ext] = unmatched_by_ext.get(ext, 0) + 1
            elif entry.outcome is Outcome.READY:
                ready.append(entry)
            elif entry.outcome is Outcome.LINKED:
                linked.append(entry)

        if unmatched_by_ext:
            total = sum(unmatched_by_ext.values())
            print(
                f"Note: skipped {total} DICOM file(s) without the DICM preamble "
                "(offset 128). These are typically DICOMDIR fragments or non-"
                "standalone DICOM exports."
            )

        if not ready:
            raise ValueError("No supported files found in the dataset")

        # Every file that will carry a distribution entry, in scan order, so
        # identifiers do not depend on which handler owns which file.
        with_objects = [
            e for e in entries if e.outcome in (Outcome.READY, Outcome.LINKED)
        ]

        # Held, not committed: an entry whose handler fails to assemble leaves
        # nothing behind, and that is not known until every batch has run.
        staged: dict = {}
        file_counter = 0
        for entry in with_objects:
            objects, file_counter = self._file_objects_for(entry, file_counter)
            staged[entry] = objects

        # Logical path -> the stored path or paths carrying it. A plain file
        # and its wrapper share one logical key.
        stored_paths: dict = defaultdict(list)
        for entry in with_objects:
            logical = str(entry.path.with_name(compression.logical_name(entry.name)))
            stored_paths[logical].append(str(entry.path))

        # A duplicate rides with the file it links to, so the FileSet covering
        # that file also covers the form the duplicate arrived in.
        dependants: dict = defaultdict(list)
        for entry in linked:
            if entry.duplicate_of is not None:
                dependants[entry.duplicate_of].append(entry)

        # TODO: future improvements per handler:
        #   - references: detect foreign-key columns (e.g. subject_id) and emit
        #     cr:references links between RecordSets — high-impact for EHR data.
        #   - enumerations: for low-cardinality categorical columns, emit
        #     sc:Enumeration RecordSets.
        by_handler: dict = defaultdict(list)
        for entry in ready:
            by_handler[entry.handler].append(entry)

        file_sets: list = []
        batches: list[tuple] = []
        for handler, batch in by_handler.items():
            pairs = [(staged[e][0].id, e.meta) for e in batch]
            try:
                # Coerced under the guard: a handler is third-party code, and
                # a malformed return must cost its own batch, not the run.
                built = BuildResult.coerce(
                    handler.build_croissant(
                        [m for _, m in pairs],
                        [fid for fid, _ in pairs],
                    ),
                    len(batch),
                )
            except Exception as e:  # noqa: BLE001 — one batch, not the bake
                logger.warning(
                    "%s.build_croissant failed: %s", type(handler).__name__, e
                )
                for entry in batch:
                    entry.failed(Reason.BUILD_FAILED, e)
                continue

            rejected = set()
            for refusal in built.declined:
                entry = batch[refusal.index]
                rejected.add(entry)
                staged.pop(entry, None)
                entry.failed(refusal.reason, ValueError(refusal.detail))

            described = [e for e in batch if e not in rejected]
            covered = [e for entry in described for e in (entry, *dependants[entry])]
            file_sets.extend(_resolve_file_sets(built.file_sets, stored_paths, covered))
            batches.append((handler, built.record_sets))
            for entry in described:
                entry.describe()

        # A duplicate stands on its primary's description. Where there is none,
        # saying so beats a link to a file nothing describes.
        for entry in linked:
            primary = entry.duplicate_of
            if primary is not None and primary.outcome is Outcome.DESCRIBED:
                continue
            staged.pop(entry, None)
            target = primary.path if primary is not None else "another file"
            entry.failed(
                Reason.BUILD_FAILED,
                ValueError(f"duplicates {target}, which was not described"),
            )

        surviving = [e for e in with_objects if e.outcome is not Outcome.FAILED]
        if not any(e.outcome is Outcome.DESCRIBED for e in surviving):
            raise ValueError("No supported files found in the dataset")

        # Reconciled here, where the staged FileObjects are final, so coverage
        # counts what was written rather than what was claimed.
        by_scanned_path = {str(e.path): e for e in entries}
        for parent in surviving:
            _own, *siblings = staged[parent]
            for obj in siblings:
                sibling = by_scanned_path.get(obj.content_url)
                if sibling is not None and sibling.outcome in _CARRIED_BY_ANOTHER:
                    sibling.referenced(parent)

        # distributions holds both FileObjects and FileSets — the full contents
        # of the Croissant `distribution` array per the spec.
        distributions = []
        by_stored_path: dict = {}
        for entry in surviving:
            objects = staged.get(entry)
            if objects is None:
                continue
            distributions.extend(objects)
            by_stored_path[str(entry.path)] = objects[0].id

        # A duplicate's sameAs target may not have been built yet: discovery
        # order is the filesystem's. Resolve once every id is assigned.
        for entry in surviving:
            if entry.outcome is Outcome.LINKED and entry in staged:
                staged[entry][0].same_as = [
                    by_stored_path[str(entry.duplicate_of.path)]
                ]

        distributions.extend(file_sets)

        # A stem shared across two formats only collides here, where every
        # batch is visible at once.
        record_sets = _disambiguate_record_sets(batches)

        described_metas = [
            (e.handler, e.meta) for e in entries if e.outcome is Outcome.DESCRIBED
        ]
        metadata = mlc.Metadata(
            name=self.name or self.dataset_path.name,
            description=self._build_description(described_metas),
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

    def _resolve_worker_count(self, n_files: int) -> int:
        """Decide how many threads to use for extraction.

        An explicit ``max_workers`` wins (floored at 1). Otherwise auto-size: 1
        for trivial inputs, else a modest oversubscription of the CPU count (the
        work is I/O-bound — whole-file hashing and header reads — so threads
        spend most of their time off-CPU), capped to bound open file descriptors.
        """
        if self.max_workers is not None:
            return max(1, self.max_workers)
        if n_files <= 1:
            return 1
        cpu = os.cpu_count() or 1
        return min(8, n_files, cpu * 2)

    def describe_refusal(self, entry: ScanEntry) -> str:
        """One line for a file that was passed over: the reason, then the file.

        Reason first because that is what a reader scans a column of these for;
        the path last because it is the long part, and it is where a reader
        goes once the reason has told them whether to care.

        The path is ``entry.path``: relative to the dataset root, so the line a
        reader is shown is the key they can look the file up by in ``--report``.
        """
        return f"{entry.detail}. File: {entry.path}"

    def _extract_entry(self, entry: ScanEntry) -> None:
        """Select a handler for one scan entry and resolve its outcome in place.

        Touches only that entry, so it is safe to call concurrently. Never
        raises: selection is inside the guard as well as extraction, because a
        handler sniffing magic bytes has to decompress to do it, so a corrupt
        wrapper raises while the registry is still deciding who owns the file.
        """
        full_path = self.dataset_path / entry.path
        try:
            selection = self.handlers.select(full_path, entry.path)
        except Exception as e:  # noqa: BLE001 — one file, not the bake
            entry.failed(Reason.CLAIM_FAILED, e)
            return

        if selection.handler is None:
            entry.unclaimed(selection.reason or Reason.NO_HANDLER, selection.refusal)
            return

        handler, source = selection.handler, selection.source
        try:
            meta = extract_with(handler, source, full_path, **self._handler_kwargs)
            # The logical path derives identifiers; the stored name is for
            # prose, which has to name a file the reader can find on disk.
            meta["relative_path"] = str(source.relative_path)
            meta["stored_name"] = entry.path.name
            entry.ready(handler, meta)
        except Exception as e:  # noqa: BLE001 — recorded, then reported
            entry.failed(Reason.EXTRACT_FAILED, e)

    def _file_objects_for(self, entry: ScanEntry, counter: int) -> tuple[list, int]:
        """Build the distribution entries for one file, and the next free id.

        A list, because a multi-file record produces several: WFDB reads a
        header together with its sibling ``.dat`` and ``.atr``. Everything here
        addresses the file *as stored*, wrapper included.
        """
        meta = entry.meta
        objects = [
            mlc.FileObject(
                id=f"file_{counter}",
                name=entry.path.name,
                content_url=str(entry.path),
                encoding_formats=_encoding_formats(
                    meta["encoding_format"], entry.path.name
                ),
                content_size=str(meta["file_size"]),
                sha256=meta["sha256"],
            )
        ]
        counter += 1

        for related in meta.get("related_files", []):
            rel_path = Path(related["path"])
            objects.append(
                mlc.FileObject(
                    id=f"file_{counter}",
                    name=related["name"],
                    content_url=str(rel_path.relative_to(self.dataset_path)),
                    encoding_formats=[related["encoding"]],
                    content_size=str(related["size"]),
                    sha256=related["sha256"],
                )
            )
            counter += 1

        return objects, counter

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


__all__ = ["MetadataGenerator", "serialize_datetime"]
