"""Tests for the steps the CLI and the MCP server share."""

from typing import List, Optional

import pytest

from croissant_baker.pipeline import parse_creators


@pytest.mark.parametrize("creators", [None, []], ids=["none", "empty"])
def test_no_creator_is_refused(creators: Optional[List[str]]) -> None:
    """The spec needs a creator, and a placeholder person is not one.

    Refused here rather than in each front door, so a caller that passes an
    empty list cannot get the generator's "Dataset Creator" stand-in.
    """
    with pytest.raises(ValueError, match="At least one '--creator'"):
        parse_creators(creators)


def test_a_named_creator_is_parsed() -> None:
    assert parse_creators(["Jane Doe,jane@example.com"]) == [
        {"name": "Jane Doe", "email": "jane@example.com"}
    ]


def test_refusals_name_the_flag_on_the_cli_and_the_argument_elsewhere() -> None:
    """Each front door names the input its caller actually typed."""
    with pytest.raises(ValueError, match="--creator"):
        parse_creators([",a@example.com"])
    with pytest.raises(ValueError) as refusal:
        parse_creators([",a@example.com"], cli=False)

    assert "--creator" not in str(refusal.value)
    assert "creators" in str(refusal.value)
