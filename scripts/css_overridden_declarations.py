#!/usr/bin/env python3
"""Find and remove CSS declarations that a later identical selector always overrides.

Usage: scripts/css_overridden_declarations.py [--write]

The stylesheets are read in the order the page applies them (CASCADE). A declaration can never
win when, for every selector of its rule, a later rule with the identical selector sets the same
property under conditions at least as broad (the same or a subset of its enclosing @media and
other conditional rules, outermost first) and with at least the same importance: same selector
means same elements and same specificity, and later wins ties.
Later values that an older engine may reject (dvh, clamp(), vendor prefixes...) are not trusted
to override, so deliberate fallbacks stay. Rules and @media blocks left empty disappear.

The script then recomputes, for every (context, selector, property), the declaration that wins
and checks that it is the same before and after.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from css_dead_selectors import CONDITIONAL, Rule, _skip, parse, split_selectors  # noqa: E402

REPOSITORY = Path(__file__).resolve().parents[1]
# Document order: index.html links branding-lock.css; main.tsx bundles styles.css then admin.css,
# then appends the other sheets in this order.
CASCADE = [
    "frontend/public/branding-lock.css",
    "frontend/src/styles.css",
    "frontend/src/features/admin/admin.css",
    "frontend/src/production-hotfix.css",
    "frontend/src/wos-premium.css",
    "frontend/src/wos-premium-torrents.css",
    "frontend/src/wos-2-1.css",
    "frontend/src/wos-2-1-final.css",
    "frontend/src/admin-settings-parity.css",
    "frontend/src/toast-system.css",
    "frontend/src/responsive-mobile.css",
    "frontend/src/admin-layout.css",
]
FALLBACK = re.compile(
    r"dvh|svh|lvh|dvw|svw|lvw|clamp\(|min\(|max\(|color-mix|oklch|oklab|lch\(|lab\(|"
    r"-webkit-|-moz-|-ms-|fit-content|subgrid|:has|light-dark|round\(|anchor",
    re.IGNORECASE,
)

Declaration = tuple[str, str, bool, int, int]  # property, value, important, start, end


def declarations(block: str) -> list[Declaration]:
    """Top-level declarations of a rule block, with offsets relative to the block."""
    out: list[Declaration] = []
    i, start, depth = 0, 0, 0
    while i <= len(block):
        if i < len(block):
            j = _skip(block, i)
            if j != i:
                i = j
                continue
            if block[i] == "(":
                depth += 1
            elif block[i] == ")":
                depth -= 1
        if i == len(block) or (block[i] == ";" and depth == 0):
            text = re.sub(r"/\*.*?\*/", "", block[start:i], flags=re.S).strip()
            if ":" in text:
                name, value = (part.strip() for part in text.split(":", 1))
                important = re.search(r"!\s*important\s*$", value, re.IGNORECASE) is not None
                value = re.sub(r"\s*!\s*important\s*$", "", value, flags=re.IGNORECASE)
                name = name if name.startswith("--") else name.lower()
                out.append((name, value, important, start, i))
            start = i + 1
        i += 1
    return out


Context = tuple[str, ...]  # enclosing conditional preludes, outermost first; () is top level
Entry = tuple[int, int, Context, tuple[str, ...], str, str, bool, int]
# order, file index, context, selectors, property, value, important, declaration index in rule


def _rules(text: str) -> list[tuple[Rule, Context]]:
    found: list[tuple[Rule, Context]] = []

    def walk(rules: list[Rule], context: Context) -> None:
        for rule in rules:
            if rule.at in CONDITIONAL:
                walk(rule.children, (*context, " ".join(rule.prelude.split())))
            elif rule.at is None:
                found.append((rule, context))

    walk(parse(text), ())
    return found


def _covers(outer: Context, inner: Context) -> bool:
    """Whenever `inner` applies, `outer` applies too: its conditions are a prefix of inner's."""
    return inner[: len(outer)] == outer


def entries(texts: list[str]) -> list[tuple[Entry, Rule]]:
    result: list[tuple[Entry, Rule]] = []
    order = 0
    for file_index, text in enumerate(texts):
        for rule, context in _rules(text):
            selectors = tuple(" ".join(s.split()) for s in split_selectors(rule.prelude))
            block = text[rule.open + 1 : rule.close]
            for index, (name, value, important, _, _) in enumerate(declarations(block)):
                result.append(
                    ((order, file_index, context, selectors, name, value, important, index), rule)
                )
                order += 1
    return result


def overridden(all_entries: list[tuple[Entry, Rule]]) -> set[tuple[int, int, int]]:
    """(file index, rule open offset, declaration index) of every overridden declaration."""
    by_key: dict[tuple[str, str], list[Entry]] = {}
    for entry, _ in all_entries:
        for selector in entry[3]:
            by_key.setdefault((selector, entry[4]), []).append(entry)

    def beaten(entry: Entry, selector: str) -> bool:
        order, _, context, _, name, _, important, _ = entry
        return any(
            other[0] > order
            and _covers(other[2], context)
            and (other[6] or not important)
            and FALLBACK.search(other[5]) is None
            for other in by_key[(selector, name)]
        )

    return {
        (entry[1], rule.open, entry[7])
        for entry, rule in all_entries
        if all(beaten(entry, selector) for selector in entry[3])
    }


def contexts(texts: list[str]) -> set[Context]:
    return {entry[2] for entry, _ in entries(texts)} | {()}


def winners(
    texts: list[str], every_context: set[Context]
) -> dict[tuple[Context, str, str], tuple[str, bool]]:
    """Winning (value, important) per (context, selector, property), per-selector cascade."""
    all_entries = [entry for entry, _ in entries(texts)]
    result: dict[tuple[Context, str, str], tuple[str, bool]] = {}
    for context in every_context:
        applicable = [e for e in all_entries if _covers(e[2], context)]
        for entry in applicable:
            for selector in entry[3]:
                key = (context, selector, entry[4])
                current = result.get(key)
                # Later entries come later in `applicable`; importance outranks order.
                if current is None or entry[6] or not current[1]:
                    result[key] = (entry[5], entry[6])
    return result


def remove(text: str, file_index: int, doomed: set[tuple[int, int, int]]) -> str:
    edits: list[tuple[int, int]] = []
    for rule, _ in _rules(text):
        block_start = rule.open + 1
        block = text[block_start : rule.close]
        found = declarations(block)
        kill = [d for i, d in enumerate(found) if (file_index, rule.open, i) in doomed]
        if not kill:
            continue
        if len(kill) == len(found) and not re.sub(r"/\*.*?\*/|\s", "", _rest(block, kill)):
            edits.append((rule.lead, rule.close + 1))
            continue
        for _, _, _, start, end in kill:
            absolute_start = block_start + start + _leading_space(block, start)
            absolute_end = block_start + end + (1 if end < len(block) else 0)
            line_start = text.rfind("\n", 0, absolute_start) + 1
            line_end = text.find("\n", absolute_end)
            line_end = len(text) if line_end < 0 else line_end
            prefix = text[line_start:absolute_start]
            suffix = text[absolute_end:line_end]
            if prefix.strip() in {"", ";"} and suffix.strip() == "":
                edits.append((line_start, min(line_end + 1, len(text))))
            else:
                while absolute_end < len(text) and text[absolute_end] == " ":
                    absolute_end += 1
                edits.append((absolute_start, absolute_end))
    out = text
    for start, stop in sorted(set(edits), reverse=True):
        out = out[:start] + out[stop:]
    return _drop_empty_media(out)


def _rest(block: str, kill: list[Declaration]) -> str:
    rest = block
    for _, _, _, start, end in sorted(kill, key=lambda d: d[3], reverse=True):
        rest = rest[:start] + rest[end + 1 :]
    return rest


def _leading_space(block: str, start: int) -> int:
    count = 0
    while start + count < len(block) and block[start + count].isspace():
        count += 1
    return count


def _drop_empty_media(text: str) -> str:
    for rule in reversed(parse(text)):
        if rule.at in CONDITIONAL and not rule.children:
            inner = text[rule.open + 1 : rule.close]
            if not re.sub(r"/\*.*?\*/|\s", "", inner, flags=re.S):
                text = text[: rule.lead] + text[rule.close + 1 :]
    return text


def main() -> int:
    write = "--write" in sys.argv[1:]
    paths = [REPOSITORY / name for name in CASCADE]
    texts = [path.read_text(encoding="utf-8") for path in paths]
    doomed = overridden(entries(texts))
    results = [remove(text, index, doomed) for index, text in enumerate(texts)]

    every_context = contexts(texts) | contexts(results)
    if winners(results, every_context) != winners(texts, every_context):
        raise SystemExit("equivalence check failed: a winning declaration changed")
    remaining = len(entries(results))
    if remaining != len(entries(texts)) - len(doomed):
        raise SystemExit(
            f"expected {len(entries(texts)) - len(doomed)} declarations, got {remaining}"
        )
    if overridden(entries(results)):
        raise SystemExit("overridden declarations survived")

    before_lines = sum(t.count("\n") for t in texts)
    after_lines = sum(t.count("\n") for t in results)
    before_imp = sum(t.count("!important") for t in texts)
    after_imp = sum(t.count("!important") for t in results)
    print(
        f"overridden declarations: {len(doomed)}; lines {before_lines} -> {after_lines}; "
        f"!important {before_imp} -> {after_imp}"
    )
    if write:
        for path, text, result in zip(paths, texts, results, strict=True):
            if result != text:
                path.write_text(result, encoding="utf-8")
    elif doomed:
        print(
            "Overridden declarations found: run scripts/css_overridden_declarations.py --write",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
