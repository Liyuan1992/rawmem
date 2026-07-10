"""Measure append and incremental-read behavior at a configured ledger scale."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rawmem.ledger import append_event, build_event, content_hash, iter_events, verify_ledger


def seed_ledger(path: Path, count: int) -> None:
    """Create a valid benchmark ledger quickly without timing fixture generation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    previous = None
    with path.open("wb", buffering=1024 * 1024) as handle:
        for index in range(count):
            event = {
                "schema": "rawmem.event.v1",
                "event_id": f"evt_benchmark_{index:09d}",
                "ts": "2026-07-10T00:00:00Z",
                "source": "benchmark",
                "event_type": "synthetic",
                "project": "fictional-benchmark",
                "cwd": "D:\\Dev\\Projects\\fictional-benchmark",
                "summary": f"Synthetic event {index}",
                "raw_text": f"synthetic payload {index}",
                "tags": ["fixture"],
                "artifacts": [],
                "payload": {"index": index},
                "privacy": {"scope": "local_only", "review_required": True},
                "previous_hash": previous,
            }
            event["content_hash"] = content_hash(event)
            previous = event["content_hash"]
            handle.write((json.dumps(event, sort_keys=True) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def timed_ms(callable_):
    started = time.perf_counter()
    result = callable_()
    return (time.perf_counter() - started) * 1000.0, result


def run_benchmark(path: Path, *, events: int, samples: int, verify: bool) -> dict:
    seed_started = time.perf_counter()
    seed_ledger(path, events)
    seed_seconds = time.perf_counter() - seed_started

    append_ms: list[float] = []
    for index in range(samples):
        elapsed, _event = timed_ms(
            lambda index=index: append_event(
                path,
                build_event(
                    source="benchmark",
                    event_type="timed_append",
                    project="fictional-benchmark",
                    raw_text=f"timed append {index}",
                ),
            )
        )
        append_ms.append(elapsed)

    cursor = None
    read_ms: list[float] = []
    total_read = 0
    for _index in range(samples):
        elapsed, batch = timed_ms(
            lambda cursor=cursor: iter_events(path, after_cursor=cursor, limit=100)
        )
        read_ms.append(elapsed)
        total_read += len(batch.events)
        cursor = batch.next_cursor
        if not batch.truncated:
            cursor = None

    tracemalloc.start()
    memory_batch = iter_events(path, limit=100)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    verification = None
    verify_seconds = None
    if verify:
        started = time.perf_counter()
        verification = verify_ledger(path)
        verify_seconds = time.perf_counter() - started

    size = path.stat().st_size
    return {
        "schema_version": "rawmem.ledger_benchmark.v1",
        "ledger": str(path),
        "seed_events": events,
        "timed_appends": samples,
        "final_events": events + samples,
        "ledger_bytes": size,
        "seed_seconds": round(seed_seconds, 3),
        "append_ms": {
            "p50": round(statistics.median(append_ms), 3),
            "p95": round(percentile(append_ms, 0.95), 3),
            "max": round(max(append_ms), 3),
        },
        "cursor_batch_100_ms": {
            "p50": round(statistics.median(read_ms), 3),
            "p95": round(percentile(read_ms, 0.95), 3),
            "max": round(max(read_ms), 3),
        },
        "cursor_events_read": total_read,
        "cursor_peak_memory_bytes": peak,
        "cursor_batch_schema": memory_batch.as_dict()["schema_version"],
        "verify_seconds": round(verify_seconds, 3) if verify_seconds is not None else None,
        "verify_valid": verification.valid if verification is not None else None,
        "verify_event_count": verification.event_count if verification is not None else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", help="Benchmark ledger path. Defaults to a temporary directory.")
    parser.add_argument("--events", type=int, default=100_000)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.events < 1 or args.samples < 1:
        parser.error("--events and --samples must be positive")
    if args.ledger:
        path = Path(args.ledger)
        result = run_benchmark(path, events=args.events, samples=args.samples, verify=args.verify)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    with tempfile.TemporaryDirectory(prefix="rawmem-benchmark-") as tmp:
        result = run_benchmark(
            Path(tmp) / "events.jsonl",
            events=args.events,
            samples=args.samples,
            verify=args.verify,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
