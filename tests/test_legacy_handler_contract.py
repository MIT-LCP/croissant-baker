"""A handler written against the old contract keeps working, and is told to move."""

from pathlib import Path

import mlcroissant as mlc
import pytest

from croissant_baker.handlers.base_handler import FileTypeHandler, InputKind
from croissant_baker.handlers.registry import HandlerRegistry, builtin_handlers
from croissant_baker.metadata_generator import MetadataGenerator
from croissant_baker.scan import Outcome


class LegacyXYZHandler(FileTypeHandler):
    """A third-party handler that only knows the pre-FileSource contract."""

    EXTENSIONS = (".xyz",)
    FORMAT_NAME = "XYZ"
    FORMAT_DESCRIPTION = "legacy contract fixture"

    def can_handle(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".xyz"

    def extract_metadata(self, file_path: Path, **kwargs) -> dict:
        return {
            "file_name": file_path.name,
            "file_size": file_path.stat().st_size,
            "sha256": "0" * 64,
            "encoding_format": "application/x-xyz",
            "column_types": {"a": "sc:Text"},
        }

    def build_croissant(self, file_metas: list, file_ids: list) -> tuple:
        return [], [
            mlc.RecordSet(
                id="xyz",
                name="xyz",
                fields=[
                    mlc.Field(
                        id="xyz/a",
                        name="a",
                        data_types="sc:Text",
                        source=mlc.Source(
                            file_object=file_ids[0],
                            extract=mlc.Extract(column="a"),
                        ),
                    )
                ],
            )
        ]


@pytest.fixture
def legacy_registry() -> HandlerRegistry:
    return HandlerRegistry([LegacyXYZHandler(), *builtin_handlers()])


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    (tmp_path / "thing.xyz").write_text("a\n1\n")
    return tmp_path


def test_a_legacy_handler_still_describes_its_files(
    legacy_registry: HandlerRegistry, dataset: Path
) -> None:
    metadata = MetadataGenerator(
        dataset_path=str(dataset), name="legacy", handlers=legacy_registry
    ).generate_metadata()

    assert [rs["@id"] for rs in metadata["recordSet"]] == ["xyz"]
    assert metadata["distribution"][0]["encodingFormat"] == "application/x-xyz"


def test_a_legacy_path_handler_still_receives_a_path(tmp_path: Path) -> None:
    """The registry honours INPUT_KIND, or a WFDB-style handler loses its .path."""
    seen = {}

    class LegacyPathHandler(FileTypeHandler):
        EXTENSIONS = (".rec",)
        FORMAT_NAME = "REC"
        INPUT_KIND = InputKind.PATH

        def can_handle(self, file_path: Path) -> bool:
            return file_path.suffix == ".rec"

        def extract_metadata(self, file_path: Path, **kwargs) -> dict:
            seen["path"] = file_path
            return {
                "file_name": file_path.name,
                "file_size": file_path.stat().st_size,
                "sha256": "0" * 64,
                "encoding_format": "application/x-rec",
            }

        def build_croissant(self, file_metas, file_ids):
            return [], []

    (tmp_path / "r.rec").write_text("x")

    MetadataGenerator(
        dataset_path=str(tmp_path),
        name="legacy-path",
        handlers=HandlerRegistry([LegacyPathHandler(), *builtin_handlers()]),
    ).generate_metadata()

    assert seen["path"] == tmp_path / "r.rec"


class _NewClaimsOldExtract(FileTypeHandler):
    """Halfway across: the normal state of a handler mid-migration."""

    EXTENSIONS = (".half",)
    FORMAT_NAME = "Half"

    def claims(self, source) -> bool:
        return source.suffix == ".half"

    def extract_metadata(self, file_path: Path, **kwargs) -> dict:
        return {
            "file_name": file_path.name,
            "file_size": file_path.stat().st_size,
            "sha256": "0" * 64,
            "encoding_format": "application/x-half",
        }

    def build_croissant(self, file_metas, file_ids):
        return [], []


class _OldClaimsNewExtract(FileTypeHandler):
    """The other half, migrated in the other order."""

    EXTENSIONS = (".other",)
    FORMAT_NAME = "Other"

    def can_handle(self, file_path: Path) -> bool:
        return file_path.suffix == ".other"

    def extract(self, source, **kwargs) -> dict:
        return {
            "file_name": source.name,
            "file_size": source.size,
            "sha256": source.sha256,
            "encoding_format": "application/x-other",
        }

    def build_croissant(self, file_metas, file_ids):
        return [], []


@pytest.mark.parametrize(
    ("handler_class", "suffix"),
    [(_NewClaimsOldExtract, ".half"), (_OldClaimsNewExtract, ".other")],
)
def test_a_partially_migrated_handler_still_bakes(
    handler_class, suffix: str, tmp_path: Path
) -> None:
    """Routing is per method, so a handler may migrate one at a time."""
    (tmp_path / f"probe{suffix}").write_text("payload")

    generator = MetadataGenerator(
        dataset_path=str(tmp_path),
        name="partial",
        handlers=HandlerRegistry([handler_class(), *builtin_handlers()]),
    )
    with pytest.warns(DeprecationWarning):
        generator.generate_metadata()

    entry = generator.scan_report.entries[0]
    assert entry.outcome is Outcome.DESCRIBED
    assert entry.meta["encoding_format"].startswith("application/x-")


def test_the_deprecated_text_opener_still_reads_a_wrapped_file(
    tmp_path: Path,
) -> None:
    """A handler written against the previous contract still imports and runs.

    The reviewer read compression.open_text as unused. It is not: it is what
    this shim resolves compression through, and the PR promises the shim keeps
    working. Inlining the body honours both.
    """
    import gzip

    from croissant_baker.handlers.utils import open_text_file

    path = tmp_path / "notes.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("id,name\n1,Ada\n")

    with pytest.warns(DeprecationWarning, match="open_text_file is deprecated"):
        with open_text_file(path) as fh:
            assert fh.read() == "id,name\n1,Ada\n"
