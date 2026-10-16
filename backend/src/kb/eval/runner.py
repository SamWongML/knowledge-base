"""Compare retrieval configurations on a JSONL qrels file.

Each line: ``{"query": "...", "relevant_document_ids": [...]}`` or
``{"query": "...", "relevant_chunk_ids": [...]}``; optional ``"relevance": {id: grade}``
for graded judgements and ``"filters": {...}`` (a :class:`SearchFilters`). Chunk-level
judgements score the chunk ranking; document-level judgements score the de-duplicated
document ranking.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import Any, Literal

from kb.eval.metrics import dedupe, mrr_at_k, ndcg_at_k, percentile, recall_at_k
from kb.schemas import SearchFilters, SearchRequest
from kb.search.pipeline import SearchOptions, search

Level = Literal["document", "chunk"]


@dataclass
class Qrel:
    query: str
    relevant: dict[str, float]
    level: Level
    filters: SearchFilters = field(default_factory=SearchFilters)


@dataclass
class Config:
    name: str
    request: dict[str, Any]
    options: SearchOptions = field(default_factory=SearchOptions)


def _no_cache(**kw: Any) -> SearchOptions:
    return SearchOptions(use_cache=False, log_query=False, **kw)


CONFIGS: dict[str, Config] = {
    "lexical": Config(
        "lexical", {"mode": "lexical", "rerank": False, "llm_rerank": False}, _no_cache()
    ),
    "semantic": Config(
        "semantic", {"mode": "semantic", "rerank": False, "llm_rerank": False}, _no_cache()
    ),
    "hybrid": Config(
        "hybrid", {"mode": "hybrid", "rerank": False, "llm_rerank": False}, _no_cache()
    ),
    "hybrid-convex": Config(
        "hybrid-convex",
        {"mode": "hybrid", "rerank": False, "llm_rerank": False},
        _no_cache(fusion="convex"),
    ),
    "hybrid+rerank": Config(
        "hybrid+rerank", {"mode": "hybrid", "rerank": True, "llm_rerank": False}, _no_cache()
    ),
    "hybrid+rerank+llm": Config(
        "hybrid+rerank+llm", {"mode": "hybrid", "rerank": True, "llm_rerank": True}, _no_cache()
    ),
}
DEFAULT_CONFIGS = ["lexical", "semantic", "hybrid", "hybrid+rerank", "hybrid+rerank+llm"]


def parse_qrel(obj: dict[str, Any]) -> Qrel:
    query = str(obj["query"])
    if obj.get("relevant_chunk_ids"):
        level: Level = "chunk"
        ids = obj["relevant_chunk_ids"]
    elif obj.get("relevant_document_ids"):
        level = "document"
        ids = obj["relevant_document_ids"]
    else:
        raise ValueError(f"qrel for {query!r} has no relevant_chunk_ids/relevant_document_ids")
    relevant = {str(i): 1.0 for i in ids}
    for key, grade in (obj.get("relevance") or {}).items():
        relevant[str(key)] = float(grade)
    filters = SearchFilters.model_validate(obj.get("filters") or {})
    return Qrel(query=query, relevant=relevant, level=level, filters=filters)


def load_qrels(path: str | Path) -> list[Qrel]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                out.append(parse_qrel(json.loads(line)))
            except (ValueError, KeyError) as exc:
                raise ValueError(f"{path}:{n}: {exc}") from exc
    return out


@dataclass
class ConfigResult:
    name: str
    metrics: dict[str, float]
    per_query: list[dict[str, Any]]
    degraded: int = 0


async def evaluate_config(
    qrels: list[Qrel], config: Config, ks: tuple[int, ...] = (5, 10, 50), concurrency: int = 4
) -> ConfigResult:
    limit = min(max(*ks, 10), 100)
    sem = asyncio.Semaphore(concurrency)

    async def one(q: Qrel) -> dict[str, Any]:
        req = SearchRequest(query=q.query, limit=limit, filters=q.filters, **config.request)
        async with sem:
            t0 = time.perf_counter()
            resp = await search(req, config.options)
            ms = (time.perf_counter() - t0) * 1000
        if q.level == "chunk":
            ranked = [str(h.chunk_id) for h in resp.hits]
        else:
            ranked = [str(i) for i in dedupe([h.document_id for h in resp.hits])]
        row: dict[str, Any] = {"query": q.query, "ms": ms, "degraded": resp.degraded}
        for k in ks:
            row[f"recall@{k}"] = recall_at_k(ranked, q.relevant, k)
        row["mrr@10"] = mrr_at_k(ranked, q.relevant, 10)
        row["ndcg@10"] = ndcg_at_k(ranked, q.relevant, 10)
        return row

    rows = await asyncio.gather(*(one(q) for q in qrels))
    metric_keys = [*(f"recall@{k}" for k in ks), "mrr@10", "ndcg@10"]
    metrics = {key: fmean(r[key] for r in rows) if rows else 0.0 for key in metric_keys}
    latencies = [r["ms"] for r in rows]
    metrics["p50_ms"] = percentile(latencies, 50)
    metrics["p95_ms"] = percentile(latencies, 95)
    return ConfigResult(
        name=config.name,
        metrics=metrics,
        per_query=rows,
        degraded=sum(1 for r in rows if r["degraded"]),
    )


async def evaluate(
    qrels: list[Qrel],
    config_names: list[str] | None = None,
    ks: tuple[int, ...] = (5, 10, 50),
    concurrency: int = 4,
) -> list[ConfigResult]:
    results = []
    for name in config_names or DEFAULT_CONFIGS:
        if name not in CONFIGS:
            raise ValueError(f"unknown config {name!r}; choose from {', '.join(CONFIGS)}")
        # Sequential across configs so latency numbers are not polluted by each other.
        results.append(await evaluate_config(qrels, CONFIGS[name], ks, concurrency))
    return results


def format_table(results: list[ConfigResult]) -> str:
    if not results:
        return "(no results)"
    keys = list(results[0].metrics)
    header = ["config", *keys, "degraded"]
    rows = [
        [
            r.name,
            *(f"{r.metrics[k]:.1f}" if k.endswith("_ms") else f"{r.metrics[k]:.4f}" for k in keys),
            str(r.degraded),
        ]
        for r in results
    ]
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows, strict=True)]

    def line(cells: list[str]) -> str:
        return "  ".join(
            c.ljust(w) if i == 0 else c.rjust(w)
            for i, (c, w) in enumerate(zip(cells, widths, strict=True))
        )

    sep = "  ".join("-" * w for w in widths)
    return "\n".join([line(header), sep, *(line(r) for r in rows)])
