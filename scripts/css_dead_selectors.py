#!/usr/bin/env python3
"""Find and remove CSS selectors whose classes no source file can produce.

Usage: scripts/css_dead_selectors.py [--write]

A class is dead when no frontend or backend source names it, and no template literal builds it
from a known prefix ("option-${...}"). Backend sources count because some classes come from API
values (a service status, a transfer state).

A selector is dead when it requires a dead class outside any functional pseudo-class
(":not(.x)" can still match, so parentheses are left alone). A rule keeps its surviving
selectors; it disappears only when none survive. Conditional at-rules left empty disappear too.
The script then re-parses both versions and checks that the ordered list of
(context, selector, declarations) of the result equals the original list minus dead selectors.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

CONDITIONAL = ("@media", "@supports", "@container", "@layer", "@document")


@dataclass
class Rule:
    start: int  # start of prelude (after preceding whitespace)
    lead: int  # start of the whitespace that precedes the prelude
    open: int  # index of "{"
    close: int  # index of matching "}"
    prelude: str
    children: list[Rule] = field(default_factory=list)
    at: str | None = None


def _skip(text: str, i: int) -> int:
    """Skip a comment or string starting at i; return the index after it, or i."""
    if text.startswith("/*", i):
        end = text.find("*/", i + 2)
        return len(text) if end < 0 else end + 2
    if text[i] in "\"'":
        quote, j = text[i], i + 1
        while j < len(text) and text[j] != quote:
            j += 2 if text[j] == "\\" else 1
        return j + 1
    return i


def parse(text: str, begin: int = 0, end: int | None = None) -> list[Rule]:
    end = len(text) if end is None else end
    rules: list[Rule] = []
    i = begin
    segment = begin
    while i < end:
        j = _skip(text, i)
        if j != i:
            i = j
            continue
        ch = text[i]
        if ch == ";":
            segment = i + 1  # declaration or statement at-rule (@import, @charset)
        elif ch == "{":
            depth, k = 1, i + 1
            while depth:
                m = _skip(text, k)
                if m != k:
                    k = m
                    continue
                if text[k] == "{":
                    depth += 1
                elif text[k] == "}":
                    depth -= 1
                k += 1
            close = k - 1
            raw = text[segment:i]
            stripped = raw.lstrip()
            start = segment + (len(raw) - len(stripped))
            # Keep leading comments attached to the previous context, not the selector.
            while text.startswith("/*", start):
                start = _skip(text, start)
                while start < i and text[start].isspace():
                    start += 1
            prelude = text[start:i].strip()
            rule = Rule(start=start, lead=segment, open=i, close=close, prelude=prelude)
            if prelude.startswith("@"):
                rule.at = prelude.split(None, 1)[0].lower()
                if rule.at in CONDITIONAL:
                    rule.children = parse(text, i + 1, close)
            rules.append(rule)
            i = close + 1
            segment = i
            continue
        elif ch == "}":
            segment = i + 1
        i += 1
    return rules


def split_selectors(prelude: str) -> list[str]:
    parts, depth, current = [], 0, []
    i = 0
    while i < len(prelude):
        ch = prelude[i]
        if ch in "\"'":
            j = _skip(prelude, i)
            current.append(prelude[i:j])
            i = j
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    parts.append("".join(current).strip())
    return [part for part in parts if part]


def _outside_parentheses(selector: str) -> str:
    out, depth = [], 0
    for ch in selector:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def is_dead(selector: str, dead: set[str]) -> bool:
    classes = re.findall(r"\.(-?[_a-zA-Z][_a-zA-Z0-9-]*)", _outside_parentheses(selector))
    return any(name in dead for name in classes)


def prune(text: str, dead: set[str]) -> str:
    edits: list[tuple[int, int, str]] = []

    def visit(rules: list[Rule]) -> bool:
        """Return True when every rule in this list is removed."""
        all_removed = True
        for rule in rules:
            if rule.at in CONDITIONAL:
                if rule.children and visit(rule.children):
                    edits.append((rule.lead, rule.close + 1, ""))
                    continue
                all_removed = False
                continue
            if rule.at is not None:
                all_removed = False
                continue
            selectors = split_selectors(rule.prelude)
            kept = [s for s in selectors if not is_dead(s, dead)]
            if not kept:
                edits.append((rule.lead, rule.close + 1, ""))
            elif len(kept) != len(selectors):
                line_start = text.rfind("\n", 0, rule.start) + 1
                indent = text[line_start : rule.start]
                indent = indent if indent.strip() == "" else ""
                edits.append((rule.start, rule.open, (",\n" + indent).join(kept) + " "))
                all_removed = False
            else:
                all_removed = False
        return all_removed

    visit(parse(text))
    # Drop edits nested inside a removed enclosing span.
    edits.sort(key=lambda e: (e[0], -e[1]))
    final: list[tuple[int, int, str]] = []
    for start, stop, replacement in edits:
        if final and start >= final[-1][0] and stop <= final[-1][1] and final[-1][2] == "":
            continue
        final.append((start, stop, replacement))
    out = text
    for start, stop, replacement in sorted(final, reverse=True):
        out = out[:start] + replacement + out[stop:]
    return out


def flatten(
    text: str, rules: list[Rule] | None = None, context: tuple[str, ...] = ()
) -> list[tuple[tuple[str, ...], str, str]]:
    rules = parse(text) if rules is None else rules
    result: list[tuple[tuple[str, ...], str, str]] = []
    for rule in rules:
        if rule.at in CONDITIONAL:
            result += flatten(text, rule.children, context + (" ".join(rule.prelude.split()),))
        elif rule.at is not None:
            result.append(
                (context, rule.prelude, " ".join(text[rule.open + 1 : rule.close].split()))
            )
        else:
            body = " ".join(text[rule.open + 1 : rule.close].split())
            for selector in split_selectors(rule.prelude):
                result.append((context, " ".join(selector.split()), body))
    return result


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    write = "--write" in sys.argv[1:]
    files = sorted(p for p in (repo / "frontend").rglob("*.css") if "node_modules" not in p.parts)
    frontend = [
        p
        for p in (repo / "frontend/src").rglob("*")
        if p.suffix in {".ts", ".tsx"} and ".test." not in p.name
    ] + [repo / "frontend/index.html"]
    backend = list((repo / "backend/app").rglob("*.py"))
    source = "\n".join(p.read_text(encoding="utf-8") for p in frontend + backend)
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", source))
    prefixes = set(re.findall(r"([A-Za-z][A-Za-z0-9_-]*-)\$\{", source))

    every: set[str] = set()
    for path in files:
        flat = flatten(path.read_text(encoding="utf-8"))
        for _, selector, _ in flat:
            every |= set(re.findall(r"\.(-?[_a-zA-Z][_a-zA-Z0-9-]*)", selector))
    dead = {c for c in every if c not in tokens and not any(c.startswith(p) for p in prefixes)}
    # Classes left only inside ":not(...)" stay: such selectors can still match.
    print(f"classes no source names: {len(dead)}")

    total_before = total_after = imp_before = imp_after = 0
    changed: list[Path] = []
    for path in files:
        original = path.read_text(encoding="utf-8")
        result = prune(original, dead)
        before, after = flatten(original), flatten(result)
        expected = [
            item for item in before if item[1].startswith("@") or not is_dead(item[1], dead)
        ]
        if after != expected:
            missing = [x for x in expected if x not in after][:3]
            extra = [x for x in after if x not in expected][:3]
            raise SystemExit(f"{path}: equivalence check failed\nmissing={missing}\nextra={extra}")
        if any(is_dead(item[1], dead) for item in after if not item[1].startswith("@")):
            raise SystemExit(f"{path}: dead selector survived")
        if result.count("{") != result.count("}"):
            raise SystemExit(f"{path}: unbalanced braces")
        lines_b, lines_a = original.count("\n"), result.count("\n")
        imp_b, imp_a = original.count("!important"), result.count("!important")
        total_before += lines_b
        total_after += lines_a
        imp_before += imp_b
        imp_after += imp_a
        if result != original:
            changed.append(path.relative_to(repo))
            print(
                f"{path.relative_to(repo)}: {lines_b} -> {lines_a} lines, "
                f"!important {imp_b} -> {imp_a}, selectors {len(before)} -> {len(after)}"
            )
            if write:
                path.write_text(result, encoding="utf-8")
    print(f"total: lines {total_before} -> {total_after}; !important {imp_before} -> {imp_after}")
    if changed and not write:
        print("Dead selectors found: run scripts/css_dead_selectors.py --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
