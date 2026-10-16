"""Concurrency stress test against a running kb API.

Fires N concurrent document ingests and M concurrent searches (plus optional asks), then
reports latency percentiles and errors per operation. Exit code 1 when anything failed.

    uv run python scripts/stress.py --url http://127.0.0.1:8787 --ingests 100 --searches 300

Uses ``KB_TOKEN`` (or ``KB_API_TOKEN``) for the write requests when set.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import random
import statistics
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field

import httpx

WORDS = [
    "postgres",
    "vector",
    "index",
    "query",
    "latency",
    "throughput",
    "memory",
    "cache",
    "shard",
    "replica",
    "embedding",
    "token",
    "chunk",
    "heading",
    "section",
    "answer",
    "citation",
    "rerank",
    "fusion",
    "hybrid",
    "lexical",
    "semantic",
    "worker",
    "queue",
    "lease",
    "heartbeat",
    "backoff",
    "retry",
    "timeout",
    "pool",
    "connection",
    "transaction",
]
MODES = ("hybrid", "lexical", "semantic")


@dataclass
class Stats:
    latencies: list[float] = field(default_factory=list)
    errors: Counter[str] = field(default_factory=Counter)

    def pct(self, q: float) -> float:
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        return ordered[min(len(ordered) - 1, max(0, math.ceil(q / 100 * len(ordered)) - 1))]


def _document(rng: random.Random, i: int, tag: str) -> dict:
    sections = []
    for s in range(rng.randint(2, 6)):
        text = " ".join(rng.choice(WORDS) for _ in range(rng.randint(80, 250)))
        sections.append(f"## Section {s}\n\n{text}\n")
    return {
        "title": f"Stress {tag} {i}",
        "content": f"# Stress document {i}\n\n" + "\n".join(sections),
        "tags": ["stress", tag],
        "source_uri": f"stress:{tag}:{i}",
    }


_slots: asyncio.Semaphore


async def _timed(stats: Stats, name: str, coro_factory) -> None:
    # Latency is measured from when a request gets a client slot, so it reflects server
    # time, not time spent queued behind our own concurrency cap.
    async with _slots:
        await _timed_inner(stats, name, coro_factory)


async def _timed_inner(stats: Stats, name: str, coro_factory) -> None:
    t0 = time.perf_counter()
    try:
        resp: httpx.Response = await coro_factory()
        if resp.status_code >= 400:
            stats.errors[f"{name} HTTP {resp.status_code}: {resp.text[:120]}"] += 1
            return
    except Exception as exc:
        stats.errors[f"{name} {type(exc).__name__}: {str(exc)[:120]}"] += 1
        return
    stats.latencies.append((time.perf_counter() - t0) * 1000)


async def _wait_ready(client: httpx.AsyncClient, tag: str, total: int, limit_s: float) -> float:
    """Poll until every stress document of this run is ready (or failed)."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < limit_s:
        r = await client.get("/api/documents", params={"tag": tag, "limit": 1, "status": "ready"})
        if r.status_code == 200 and r.json()["total"] >= total:
            return time.perf_counter() - t0
        await asyncio.sleep(0.5)
    return float("nan")


async def run(args: argparse.Namespace) -> int:
    global _slots
    _slots = asyncio.Semaphore(args.concurrency)
    token = os.environ.get("KB_TOKEN") or os.environ.get("KB_API_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    limits = httpx.Limits(
        max_connections=args.concurrency, max_keepalive_connections=args.concurrency
    )
    rng = random.Random(args.seed)
    tag = f"run-{uuid.uuid4().hex[:8]}"
    ingest, search, ask = Stats(), Stats(), Stats()
    async with httpx.AsyncClient(
        base_url=args.url, headers=headers, timeout=args.timeout, limits=limits
    ) as client:
        ops = [
            _timed(
                ingest,
                "ingest",
                lambda d=_document(rng, i, tag): client.post("/api/documents", json=d),
            )
            for i in range(args.ingests)
        ]
        for _ in range(args.searches):
            q = " ".join(rng.sample(WORDS, rng.randint(1, 4)))
            body = {"query": q, "mode": rng.choice(MODES), "limit": 10}
            if rng.random() < 0.3:  # cache-busting variety: filters and context expansion
                body["filters"] = {"tags": ["stress"]}
                body["expand_context"] = True
            ops.append(_timed(search, "search", lambda b=body: client.post("/api/search", json=b)))
        for _ in range(args.asks):
            question = "How does the " + " and ".join(rng.sample(WORDS, 2)) + " work?"
            ops.append(
                _timed(
                    ask, "ask", lambda qn=question: client.post("/api/ask", json={"question": qn})
                )
            )
        rng.shuffle(ops)
        t0 = time.perf_counter()
        await asyncio.gather(*ops)
        wall = time.perf_counter() - t0
        ready_s = (
            await _wait_ready(
                client, tag, args.ingests - sum(ingest.errors.values()), args.drain_timeout
            )
            if args.ingests
            else 0.0
        )

    print(
        f"wall {wall:.2f}s for {len(ops)} requests ({len(ops) / wall:.0f} req/s), concurrency {args.concurrency}"
    )
    for name, st in (("ingest", ingest), ("search", search), ("ask", ask)):
        n = len(st.latencies) + sum(st.errors.values())
        if not n:
            continue
        mean = statistics.fmean(st.latencies) if st.latencies else 0.0
        print(
            f"{name:7s} n={n:4d} ok={len(st.latencies):4d} errors={sum(st.errors.values()):3d} "
            f"p50={st.pct(50):7.1f}ms p95={st.pct(95):7.1f}ms p99={st.pct(99):7.1f}ms "
            f"max={max(st.latencies, default=0):7.1f}ms mean={mean:7.1f}ms"
        )
        for err, count in st.errors.most_common(5):
            print(f"    {count}x {err}")
    if args.ingests:
        print(f"all ingested documents ready {ready_s:.1f}s after the burst")
    failed = sum(sum(s.errors.values()) for s in (ingest, search, ask))
    return 1 if failed or math.isnan(ready_s) else 0


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--url", default=os.environ.get("KB_URL", "http://127.0.0.1:8787"))
    p.add_argument("--ingests", type=int, default=100)
    p.add_argument("--searches", type=int, default=300)
    p.add_argument("--asks", type=int, default=0)
    p.add_argument("--concurrency", type=int, default=64, help="requests in flight at once")
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--drain-timeout", type=float, default=300.0)
    p.add_argument("--seed", type=int, default=7)
    sys.exit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
