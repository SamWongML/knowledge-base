"""Query analysis: normalisation, inline operators and highlight terms.

User text never reaches a query *parser*: pg_search's ``|||`` / ``###`` operators run the
field tokenizer over the string, so any input is safe once NUL and control characters
are removed. Inline operators recognised at word starts:

- ``"exact phrase"``           → required phrase (BM25 ``###``)
- ``tag:foo`` / ``tag:"a b"``   → filters.tags (match-any, merged with request filters)
- ``in:slug`` / ``collection:slug`` → filters.collections

Anything that does not parse cleanly (``a:b``, an unbalanced quote) stays plain text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from kb.schemas import SearchFilters

MAX_TEXT_CHARS = 1000
MAX_PHRASES = 8

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_OPERATOR = re.compile(
    r'(?<!\S)(?P<key>tag|in|collection):(?:"(?P<quoted>[^"]*)"|(?P<bare>[^\s"]+))',
    re.IGNORECASE,
)
_PHRASE = re.compile(r'"([^"]*)"')
_WORD = re.compile(r"\w+", re.UNICODE)
_SPACE = re.compile(r"\s+")

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "so",
        "than",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    }
)


@dataclass
class ParsedQuery:
    raw: str
    text: str  # free text without operators/quotes, fed to the BM25 disjunction
    phrases: list[str]  # quoted phrases, each required for lexical matches
    semantic: str  # natural-language form (text + phrases) for embeddings and rerankers
    terms: list[str]  # lower-cased words used for highlighting
    filters: SearchFilters = field(default_factory=SearchFilters)

    @property
    def empty(self) -> bool:
        return not self.text and not self.phrases


def normalize(text: str) -> str:
    """NFKC, drop NUL/control characters (Postgres rejects NUL), collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch if ch in "\t\n" or unicodedata.category(ch)[0] != "C" else " " for ch in text)
    return _SPACE.sub(" ", text).strip()


def stem(word: str) -> str:
    """Very light suffix stripper, only used to align highlights with BM25 stemming."""
    w = word.lower()
    for suffix in ("ingly", "edly", "ing", "ers", "ies", "ied", "ed", "es", "er", "ly", "s"):
        if len(w) - len(suffix) >= 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def highlight_terms(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for word in _WORD.findall(text.lower()):
        if (len(word) > 1 or word.isdigit()) and word not in _STOPWORDS:
            seen.setdefault(word, None)
    return list(seen)[:32]


def _merge(current: list[str] | None, extra: list[str]) -> list[str] | None:
    if not extra:
        return current
    merged = dict.fromkeys([*(current or []), *extra])
    return list(merged)


def parse_query(raw: str, filters: SearchFilters | None = None) -> ParsedQuery:
    text = normalize(raw)
    tags: list[str] = []
    collections: list[str] = []

    def take_operator(m: re.Match[str]) -> str:
        key = m.group("key").lower()
        value = (m.group("quoted") if m.group("quoted") is not None else m.group("bare")).strip()
        if not value:
            return m.group(0)
        if key == "tag":
            tags.append(value)
            return " "
        slug = value.lower()
        if _SLUG.match(slug):
            collections.append(slug)
            return " "
        return m.group(0)

    text = _OPERATOR.sub(take_operator, text)

    phrases: list[str] = []

    def take_phrase(m: re.Match[str]) -> str:
        phrase = _SPACE.sub(" ", m.group(1)).strip()
        if not phrase:
            return " "
        if len(_WORD.findall(phrase)) > 1 and len(phrases) < MAX_PHRASES:
            phrases.append(phrase)
            return " "
        return f" {phrase} "  # a one-word "phrase" is just a term

    text = _PHRASE.sub(take_phrase, text)
    text = _SPACE.sub(" ", text.replace('"', " ")).strip()[:MAX_TEXT_CHARS]

    merged = (filters or SearchFilters()).model_copy()
    merged.tags = _merge(merged.tags, tags)
    merged.collections = _merge(merged.collections, collections)

    semantic = " ".join([text, *phrases]).strip()
    return ParsedQuery(
        raw=raw,
        text=text,
        phrases=phrases,
        semantic=semantic,
        terms=highlight_terms(semantic),
        filters=merged,
    )
