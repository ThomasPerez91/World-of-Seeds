#!/usr/bin/env python3

from __future__ import annotations

import configparser
import sys
from pathlib import Path


class NewGreedyPolicyError(RuntimeError):
    """Raised when the Rise2 NewGreedy configuration violates a fixed policy."""


def validate_config(path: Path) -> None:
    parser = configparser.ConfigParser(
        interpolation=None,
        inline_comment_prefixes=(";", "#"),
        strict=True,
    )
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
        flow_detail = parser.getint("proxy", "flow_detail")
        persist_stats = parser.getboolean("stats", "persist_stats")
        auto_purge_stopped = parser.getboolean("stats", "auto_purge_stopped")
    except (OSError, configparser.Error, ValueError) as exc:
        raise NewGreedyPolicyError("required NewGreedy policy keys are missing or invalid") from exc

    if flow_detail != 0:
        raise NewGreedyPolicyError("proxy.flow_detail must be 0")
    if not persist_stats:
        raise NewGreedyPolicyError("stats.persist_stats must be true")
    if auto_purge_stopped:
        raise NewGreedyPolicyError("stats.auto_purge_stopped must be false")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage: rise2_v2_newgreedy_policy.py CONFIG", file=sys.stderr)
        return 2
    try:
        validate_config(Path(args[0]))
    except NewGreedyPolicyError as exc:
        print(f"NewGreedy config policy failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
