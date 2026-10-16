from __future__ import annotations

import math

import pytest

from kb.eval.generate import _parse_question, copies_passage
from kb.eval.metrics import (
    dcg,
    dedupe,
    mrr_at_k,
    ndcg_at_k,
    percentile,
    precision_at_k,
    recall_at_k,
)
from kb.eval.runner import ConfigResult, format_table, parse_qrel


def test_recall_precision_mrr():
    ranked = ["a", "b", "c", "d"]
    rel = {"b": 1, "d": 1, "z": 1}
    assert recall_at_k(ranked, rel, 2) == pytest.approx(1 / 3)
    assert recall_at_k(ranked, rel, 10) == pytest.approx(2 / 3)
    assert recall_at_k(ranked, {}, 10) == 0.0
    assert precision_at_k(ranked, rel, 4) == 0.5
    assert mrr_at_k(ranked, rel) == 0.5
    assert mrr_at_k(ranked, {"x": 1}) == 0.0
    assert mrr_at_k(ranked, {"d": 1}, k=3) == 0.0


def test_ndcg():
    assert ndcg_at_k(["a", "b"], {"a": 1}) == 1.0
    # Relevant doc at rank 2: 1/log2(3).
    assert ndcg_at_k(["x", "a"], {"a": 1}) == pytest.approx(1 / math.log2(3))
    # Graded: ideal order is (3, 1); got (1, 3).
    got = (2**1 - 1) / 1 + (2**3 - 1) / math.log2(3)
    ideal = (2**3 - 1) / 1 + (2**1 - 1) / math.log2(3)
    assert ndcg_at_k(["lo", "hi"], {"hi": 3, "lo": 1}) == pytest.approx(got / ideal)
    assert ndcg_at_k(["a"], {"a": 0}) == 0.0
    assert dcg([]) == 0.0


def test_helpers():
    assert dedupe([1, 2, 1, 3, 2]) == [1, 2, 3]
    assert percentile([], 50) == 0.0
    assert percentile([5, 1, 3], 50) == 3
    assert percentile(list(range(1, 101)), 95) == 95


def test_parse_qrel_variants():
    q = parse_qrel({"query": "x", "relevant_chunk_ids": [1, 2], "relevance": {"2": 3}})
    assert q.level == "chunk" and q.relevant == {"1": 1.0, "2": 3.0}
    q = parse_qrel({"query": "y", "relevant_document_ids": ["d"], "filters": {"tags": ["t"]}})
    assert q.level == "document" and q.filters.tags == ["t"]
    with pytest.raises(ValueError):
        parse_qrel({"query": "z"})


def test_format_table():
    res = [ConfigResult("lexical", {"recall@10": 0.5, "p50_ms": 12.34}, [], 0)]
    table = format_table(res)
    assert "lexical" in table and "0.5000" in table and "12.3" in table


def test_copy_filter_and_question_parsing():
    passage = "Feed the sourdough starter with flour and water every day until it doubles."
    assert copies_passage("feed the sourdough starter with flour and water", passage)
    assert not copies_passage("How often should I feed my levain?", passage)
    assert _parse_question('{"question": " How? "}') == "How?"
    assert _parse_question('"plain text question"\nextra') == "plain text question"
    assert _parse_question("") == ""
