"""Hybrid retrieval: BM25 (pg_search) + dense (pgvector HNSW) → fusion → rerank cascade.

Entry point: :func:`kb.search.pipeline.search`.
"""
