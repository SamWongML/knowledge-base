"""IR metrics over a ranked list of ids and graded relevance judgements (grade > 0 = relevant)."""

from __future__ import annotations

import math
from collections.abc import Hashable, Mapping, Sequence


def recall_at_k[K: Hashable](ranked: Sequence[K], relevant: Mapping[K, float], k: int) -> float:
    rel = {i for i, g in relevant.items() if g > 0}
    if not rel:
        return 0.0
    return len(rel.intersection(ranked[:k])) / len(rel)


def precision_at_k[K: Hashable](ranked: Sequence[K], relevant: Mapping[K, float], k: int) -> float:
    if k <= 0:
        return 0.0
    return sum(1 for i in ranked[:k] if relevant.get(i, 0) > 0) / k


def mrr_at_k[K: Hashable](ranked: Sequence[K], relevant: Mapping[K, float], k: int = 10) -> float:
    for pos, i in enumerate(ranked[:k], start=1):
        if relevant.get(i, 0) > 0:
            return 1.0 / pos
    return 0.0


def dcg(gains: Sequence[float]) -> float:
    return sum((2**g - 1) / math.log2(pos + 2) for pos, g in enumerate(gains))


def ndcg_at_k[K: Hashable](ranked: Sequence[K], relevant: Mapping[K, float], k: int = 10) -> float:
    """nDCG with exponential gain ``2^g - 1`` (binary judgements reduce to the classic form)."""
    ideal = dcg(sorted((g for g in relevant.values() if g > 0), reverse=True)[:k])
    if ideal == 0:
        return 0.0
    return dcg([relevant.get(i, 0) for i in ranked[:k]]) / ideal


def dedupe[K: Hashable](ids: Sequence[K]) -> list[K]:
    """Keep the first occurrence of each id (chunk hits → document ranking)."""
    return list(dict.fromkeys(ids))


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(q / 100 * len(ordered)) - 1))
    return ordered[idx]
