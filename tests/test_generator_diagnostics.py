"""MetadataGenerator diagnostics go through ``logging``, like the handlers do.

Warnings previously went to stdout via ``print()`` while every handler logged
through a module logger. These tests pin the consistent behaviour: warnings are
emitted on the ``croissant_baker.metadata_generator`` logger (controllable,
off-stdout) rather than printed.
"""

import logging

from croissant_baker.metadata_generator import _apply_field_mappings

_GEN_LOGGER = "croissant_baker.metadata_generator"


def test_field_mapping_multi_match_warns_via_logging(
    caplog,
) -> None:
    """A field mapping that resolves to multiple fields logs a WARNING."""
    metadata = {
        "recordSet": [
            {"field": [{"@type": "cr:Field", "name": "age"}]},
            {"field": [{"@type": "cr:Field", "name": "age"}]},
        ]
    }

    with caplog.at_level(logging.WARNING, logger=_GEN_LOGGER):
        _apply_field_mappings(metadata, {"age": {"equivalent_property": "wdt:P3629"}})

    matching = [
        r
        for r in caplog.records
        if r.name == _GEN_LOGGER
        and "age" in r.getMessage()
        and "2 fields" in r.getMessage()
    ]
    assert matching, caplog.records
    assert matching[0].levelno == logging.WARNING
