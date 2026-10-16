"""Rank fusion of retriever outputs.

- Reciprocal Rank Fusion: ``Σ w_r / (k + rank_r)`` (rank is 1-based). Robust, zero-config.
- Convex combination of min-max normalised scores: ``Σ w_r · norm(score_r)``; Bruch et al.
  (TOIS 2023) find it beats RRF once the weights are tuned on a small labelled set.
"""

from __future__ import annotations

from typing import Literal

Ranked = list[tuple[int, float]]  # (chunk_id, score), best first
FusionMethod = Literal["rrf", "convex"]


def rrf(
    rankings: dict[str, Ranked], k: int = 60, weights: dict[str, float] | None = None
) -> dict[int, float]:
    fused: dict[int, float] = {}
    for name, ranked in rankings.items():
        w = (weights or {}).get(name, 1.0)
        for rank, (cid, _) in enumerate(ranked, start=1):
            fused[cid] = fused.get(cid, 0.0) + w / (k + rank)
    return fused


def minmax(ranked: Ranked) -> dict[int, float]:
    if not ranked:
        return {}
    values = [s for _, s in ranked]
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return {cid: 1.0 for cid, _ in ranked}
    return {cid: (s - lo) / (hi - lo) for cid, s in ranked}


def convex(
    rankings: dict[str, Ranked], weights: dict[str, float] | None = None
) -> dict[int, float]:
    """Missing documents contribute 0 for that retriever."""
    fused: dict[int, float] = {}
    for name, ranked in rankings.items():
        w = (weights or {}).get(name, 1.0 / max(len(rankings), 1))
        for cid, s in minmax(ranked).items():
            fused[cid] = fused.get(cid, 0.0) + w * s
    return fused


def fuse(
    rankings: dict[str, Ranked],
    *,
    method: FusionMethod = "rrf",
    k: int = 60,
    weights: dict[str, float] | None = None,
) -> list[tuple[int, float, dict[str, float]]]:
    """Fuse and return ``[(chunk_id, fused_score, per_retriever_scores)]`` best first.

    Per-retriever scores carry both the raw score (``bm25``/``dense``) and the 1-based rank
    (``bm25_rank``/``dense_rank``) so clients and evals can inspect every stage.
    """
    fused = rrf(rankings, k, weights) if method == "rrf" else convex(rankings, weights)
    detail: dict[int, dict[str, float]] = {cid: {} for cid in fused}
    for name, ranked in rankings.items():
        for rank, (cid, score) in enumerate(ranked, start=1):
            detail[cid][name] = score
            detail[cid][f"{name}_rank"] = float(rank)
    # Stable tie-break: best individual rank, then id.
    order = sorted(
        fused,
        key=lambda c: (
            -fused[c],
            min((v for k_, v in detail[c].items() if k_.endswith("_rank")), default=1e9),
            c,
        ),
    )
    return [(cid, fused[cid], detail[cid] | {method: fused[cid]}) for cid in order]
