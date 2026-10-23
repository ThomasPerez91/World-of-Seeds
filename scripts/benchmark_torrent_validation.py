#!/usr/bin/env python3
"""Measure network-free metainfo validation: backend/.venv/bin/python scripts/benchmark_torrent_validation.py."""

import argparse
import hashlib
import json
import sys
import tracemalloc
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.torrents.metainfo import MAX_BENCODE_ITEMS, BValue, _encode, sanitize_torrent


def benchmark(
    count: int, *, depth: int = 1, measure_memory: bool = False
) -> dict[str, int | float | bool]:
    if not 1 <= count <= 30_000:
        raise ValueError("file count must be between 1 and 30000")
    if not 1 <= depth <= 1000:
        raise ValueError("depth must be between 1 and 1000")
    if count * (depth + 4) + 16 > MAX_BENCODE_ITEMS:
        raise ValueError("fixture exceeds the parser item budget")
    info: dict[bytes, BValue] = {
        b"files": [
            {
                b"length": 1,
                b"path": [b"data" if depth == 1 else f"group-{index}".encode()]
                + [b"a"] * (depth - 1)
                + [f"file-{index:05d}.bin".encode()],
            }
            for index in range(count)
        ],
        b"name": b"Benchmark",
        b"piece length": 16_384,
        b"pieces": b"p" * (20 * ((count + 16_383) // 16_384)),
    }
    info_bytes = _encode(info)
    content = _encode({b"announce": b"https://c411.org/announce", b"info": info})
    if measure_memory:
        tracemalloc.start()
    started = perf_counter()
    parsed = sanitize_torrent(
        content, allowed_tracker_hosts=["c411.org"], max_total_size=150_000_000_000
    )
    elapsed = perf_counter() - started
    peak = tracemalloc.get_traced_memory()[1] if measure_memory else 0
    if measure_memory:
        tracemalloc.stop()
    preserved = (
        parsed.info_hash == hashlib.sha1(info_bytes, usedforsecurity=False).hexdigest()
    )
    assert preserved and len(parsed.files) == count and parsed.total_size == count
    result: dict[str, int | float | bool] = {
        "files": count,
        "seconds": round(elapsed, 6),
        "infohash_preserved": preserved,
    }
    if measure_memory:
        result["peak_bytes"] = peak
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--files", type=int, nargs="+", default=[1000, 5000, 10_000, 30_000]
    )
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--memory", action="store_true")
    arguments = parser.parse_args()
    print(
        json.dumps(
            [
                benchmark(count, depth=arguments.depth, measure_memory=arguments.memory)
                for count in arguments.files
            ]
        )
    )


if __name__ == "__main__":
    main()
