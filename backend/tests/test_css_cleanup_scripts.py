"""Proofs used by the CSS cleanup checks in scripts/."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
OVERRIDDEN: dict[str, Any] = runpy.run_path(str(SCRIPTS / "css_overridden_declarations.py"))
DEAD: dict[str, Any] = runpy.run_path(str(SCRIPTS / "css_dead_selectors.py"))


def _overridden(*sheets: str) -> list[tuple[str, str]]:
    """(selector list, property) of every declaration the check would remove."""
    texts = list(sheets)
    doomed = OVERRIDDEN["overridden"](OVERRIDDEN["entries"](texts))
    return sorted(
        (", ".join(entry[3]), entry[4])
        for entry, rule in OVERRIDDEN["entries"](texts)
        if (entry[1], rule.open, entry[7]) in doomed
    )


def test_a_later_identical_selector_overrides_the_same_property() -> None:
    assert _overridden(".a { color: red; margin: 0; }", ".a { color: blue; }") == [(".a", "color")]


def test_mutually_exclusive_nested_conditions_are_not_duplicates() -> None:
    sheet = """
    @media (min-width: 900px) { @supports (display: grid) { .sample { color: blue; } } }
    @supports (display: grid) { @media (max-width: 600px) { .sample { color: green; } } }
    @media (max-width: 600px) { @supports (display: grid) { .sample { color: green; } } }
    """
    assert _overridden(sheet) == []


@pytest.mark.parametrize(
    "later",
    [
        ".sample { color: green; }",
        "@media (min-width: 900px) { .sample { color: green; } }",
        "@media (min-width: 900px) { @supports (display: grid) { .sample { color: green; } } }",
    ],
)
def test_conditions_that_contain_the_earlier_ones_override(later: str) -> None:
    earlier = "@media (min-width: 900px) { @supports (display: grid) { .sample { color: blue; } } }"
    assert _overridden(earlier, later) == [(".sample", "color")]


@pytest.mark.parametrize(
    "later",
    [
        "@supports (display: grid) { .sample { color: green; } }",
        "@media (max-width: 600px) { .sample { color: green; } }",
    ],
)
def test_other_conditions_do_not_override(later: str) -> None:
    earlier = "@media (min-width: 900px) { @supports (display: grid) { .sample { color: blue; } } }"
    assert _overridden(earlier, later) == []


def test_fallbacks_and_importance_are_kept() -> None:
    assert _overridden(".a { height: 100vh; }", ".a { height: 100dvh; }") == []
    assert _overridden(".a { color: red !important; }", ".a { color: blue; }") == []
    assert _overridden(".a { color: red; }", ".a { color: blue !important; }") == [(".a", "color")]


def test_a_rule_is_overridden_only_for_every_one_of_its_selectors() -> None:
    assert _overridden(".a, .b { color: red; }", ".a { color: blue; }") == []
    assert _overridden(".a, .b { color: red; }", ".a, .b { color: blue; }") == [(".a, .b", "color")]


def test_dead_selector_detection_ignores_classes_inside_functional_pseudo_classes() -> None:
    dead = {"gone"}
    is_dead = DEAD["is_dead"]
    assert is_dead(".gone .row", dead)
    assert is_dead(".row.gone::before", dead)
    assert not is_dead(".row:not(.gone)", dead)
    assert not is_dead(".row:is(.gone, .kept)", dead)


def test_dead_selector_pruning_keeps_live_selectors_and_drops_empty_media() -> None:
    sheet = (
        ".kept, .gone { color: red; }\n@media (max-width: 600px) {\n  .gone { color: blue; }\n}\n"
    )
    result = DEAD["prune"](sheet, {"gone"})
    assert ".kept" in result and ".gone" not in result and "@media" not in result
