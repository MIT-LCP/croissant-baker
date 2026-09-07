"""TSV file handler — extends CSVHandler with tab delimiter."""

from croissant_baker.handlers.csv_handler import CSVHandler


class TSVHandler(CSVHandler):
    """
    Handler for TSV files.

    TSV is structurally identical to CSV with a tab delimiter. This handler
    inherits all type inference, row counting, and Croissant
    generation logic from CSVHandler. The only differences are the file
    extensions it claims and the delimiter passed to PyArrow.

    Supported: .tsv, wrapped or not. Compression is resolved before this
    handler is asked.

    To add another delimiter-separated format (e.g. pipe-separated): subclass
    CSVHandler, override _suffix() and _delimiter(), register the instance
    in registry.py. No other files need to change.
    """

    EXTENSIONS = (".tsv",)
    FORMAT_NAME = "TSV"
    FORMAT_DESCRIPTION = "Column names, inferred types, optional row count"

    @staticmethod
    def _suffix() -> str:
        return ".tsv"

    @staticmethod
    def _delimiter() -> str:
        return "\t"

    @staticmethod
    def _encoding_format() -> str:
        return "text/tab-separated-values"
