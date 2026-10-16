"""Agent-facing result shapes and compact text renderings, shared by the MCP server and the CLI.

Pure functions over JSON-like dicts (the API's ``schemas`` models dumped with
``mode="json"``), so the CLI can use them without importing pydantic or the MCP SDK.
The MCP ``outputSchema`` and the CLI's ``--json`` output are therefore the same shape.
"""

from __future__ import annotations

import html
import re
from typing import Any

SNIPPET_CHARS = 300
_MARK_RE = re.compile(r"</?mark>")

Json = dict[str, Any]


def plain_snippet(snippet: str, limit: int = SNIPPET_CHARS) -> str:
    """Strip ``<mark>`` highlighting and HTML escaping; collapse whitespace; truncate."""
    text = " ".join(html.unescape(_MARK_RE.sub("", snippet or "")).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def title_line(title: str, heading_path: str) -> str:
    return f"{title} › {heading_path}" if heading_path else title


def with_hint(text: str, hint: str | None) -> str:
    return f"{text}\n\nHint: {hint}" if hint else text


# --- search ----------------------------------------------------------------------------


def search_result(resp: Json) -> Json:
    """``SearchResponse`` -> ``{query, hits[], degraded[], hint}``."""
    hits = [
        {
            "chunk_id": h["chunk_id"],
            "document_id": str(h["document_id"]),
            "title": h["document_title"],
            "heading_path": h.get("heading_path") or "",
            "snippet": plain_snippet(h.get("snippet") or h.get("content") or ""),
            "score": round(float(h["score"]), 4),
            "collection": h.get("collection", ""),
            "source_uri": h.get("source_uri"),
        }
        for h in resp.get("hits", [])
    ]
    degraded = list(resp.get("degraded") or [])
    hint = None
    if not hits:
        hint = "No matches. Try broader or different wording, drop filters, or mode='lexical'."
    elif degraded:
        hint = f"Degraded search (skipped: {', '.join(degraded)}); results may be less precise."
    return {"query": resp.get("query", ""), "hits": hits, "degraded": degraded, "hint": hint}


def render_search(out: Json) -> str:
    lines = [f'{len(out["hits"])} hits for "{out["query"]}"']
    for i, h in enumerate(out["hits"], 1):
        lines.append(
            f"[{i}] {title_line(h['title'], h['heading_path'])} (score {h['score']:.3f}) "
            f"doc={h['document_id']} chunk={h['chunk_id']}\n    {h['snippet']}"
        )
    return with_hint("\n".join(lines), out.get("hint"))


# --- ask -------------------------------------------------------------------------------


def ask_result(resp: Json) -> Json:
    """``AskResponse`` -> ``{answer, sources[], engine, hint}``."""
    sources = [
        {
            "n": c["n"],
            "document_id": str(c["document_id"]),
            "chunk_id": c["chunk_id"],
            "title": c["document_title"],
            "heading_path": c.get("heading_path") or "",
            "snippet": plain_snippet(c.get("snippet") or "", 200),
        }
        for c in resp.get("citations", [])
    ]
    return {"answer": resp["answer"], "sources": sources, "engine": resp["engine"], "hint": None}


def render_sources(sources: list[Json]) -> str:
    if not sources:
        return ""
    return "Sources:\n" + "\n".join(
        f"[{s['n']}] {title_line(s['title'], s['heading_path'])} (doc={s['document_id']})"
        for s in sources
    )


def render_ask(out: Json) -> str:
    src = render_sources(out["sources"])
    return f"{out['answer']}\n\n{src}" if src else out["answer"]


# --- documents -------------------------------------------------------------------------


def doc_summary(d: Json) -> Json:
    """``DocumentSummary`` -> compact listing row."""
    return {
        "id": str(d["id"]),
        "title": d["title"],
        "collection": d["collection"],
        "tags": list(d.get("tags") or []),
        "status": d["status"],
        "source_uri": d.get("source_uri"),
        "chunk_count": d.get("chunk_count", 0),
        "updated_at": str(d.get("updated_at", ""))[:19],
    }


def list_result(page: Json) -> Json:
    """``Page[DocumentSummary]`` -> ``{items[], total, offset, next_offset, hint}``."""
    items = [doc_summary(d) for d in page.get("items", [])]
    offset = int(page.get("offset", 0))
    nxt = offset + len(items)
    next_offset = nxt if nxt < int(page.get("total", 0)) else None
    return {
        "items": items,
        "total": page.get("total", 0),
        "offset": offset,
        "next_offset": next_offset,
        "hint": f"More documents: call again with offset={next_offset}." if next_offset else None,
    }


def render_list(out: Json) -> str:
    lines = [f"{len(out['items'])} of {out['total']} documents"]
    for d in out["items"]:
        tags = f" [{', '.join(d['tags'])}]" if d["tags"] else ""
        lines.append(
            f"- {d['title']}{tags} ({d['collection']}, {d['status']}, {d['updated_at']}) id={d['id']}"
        )
    return with_hint("\n".join(lines), out.get("hint"))


def doc_header(d: Json) -> str:
    """One-line metadata header for a document (``Document`` or read result)."""
    head = (
        f"# {d['title']}\n(doc={d.get('document_id') or d.get('id')} "
        f"collection={d['collection']} status={d['status']}"
    )
    if d.get("tags"):
        head += f" tags={','.join(d['tags'])}"
    if d.get("source_uri"):
        head += f" source={d['source_uri']}"
    return head + ")"


# --- collections -----------------------------------------------------------------------


def collection_row(c: Json) -> Json:
    return {
        "slug": c["slug"],
        "name": c["name"],
        "description": c.get("description", ""),
        "document_count": c.get("document_count", 0),
    }


def render_collections(rows: list[Json]) -> str:
    return (
        "\n".join(f"- {c['slug']}: {c['name']} ({c['document_count']} docs)" for c in rows)
        or "no collections"
    )
