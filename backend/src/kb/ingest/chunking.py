"""Structure-aware Markdown chunking.

The document is split into sections at ATX headings (``#`` … ``######``); each section
carries a breadcrumb of its enclosing headings (``"Guide > Install > macOS"``). Inside a
section, text is cut into *units* (sentences, list items, table rows, whole fenced code
blocks) that are greedily packed up to ``target_tokens``. Chunks prefer to end on a
paragraph boundary, consecutive chunks of one section overlap by about
``overlap_tokens``, and fenced code blocks are never split (unless a single block is so
large that it would exceed the hard limit, in which case it is split on line boundaries
and every piece is re-fenced so it still renders as code).

Every chunk's ``content`` is the exact source slice ``text[char_start:char_end]`` (except
for re-fenced pieces of giant code blocks), so the UI can highlight hits in the original.
The algorithm is deterministic: the same input always yields the same chunks.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LIST_OR_TABLE = re.compile(r"^\s*(?:[-*+]\s|\d{1,9}[.)]\s|\|)")
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])[\"')\]]*\s+")
_WORD = re.compile(r"\S+\s*")


def approx_tokens(text: str) -> int:
    """Cheap tokenizer-free estimate (~4 characters per token for English BPE vocabularies)."""
    if not text:
        return 0
    return max(1, math.ceil(len(text.strip()) / 4))


@dataclass(frozen=True, slots=True)
class Chunk:
    ord: int
    heading_path: str
    content: str
    token_count: int
    char_start: int
    char_end: int


@dataclass(slots=True)
class _Unit:
    start: int
    end: int
    tokens: int
    para_start: bool  # first unit of a block: a good place to begin a chunk
    code: bool = False
    heading: bool = False


@dataclass(slots=True)
class _Section:
    path: tuple[str, ...]
    units: list[_Unit]


def _clean_heading(raw: str) -> str:
    text = re.sub(r"[*_`]+", "", raw).strip()
    # [label](url) -> label
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)


def find_h1(markdown: str) -> str | None:
    """Text of the first H1 heading outside code fences."""
    fence: str | None = None
    for line in markdown.splitlines():
        m = _FENCE.match(line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        h = _HEADING.match(line)
        if h and len(h.group(1)) == 1:
            title = _clean_heading(h.group(2))
            if title:
                return title[:500]
    return None


def extract_title(markdown: str, filename: str | None = None) -> str:
    """First H1 outside code fences, else the file name stem, else the first line of text."""
    h1 = find_h1(markdown)
    if h1:
        return h1
    if filename:
        stem = PurePosixPath(filename.replace("\\", "/")).stem.strip()
        if stem:
            return stem[:500]
    first_line = next((ln.strip() for ln in markdown.splitlines() if ln.strip()), "")
    first_line = _clean_heading(first_line.lstrip("#>-*+`~ ").strip())
    return (first_line[:120] or "Untitled").strip()


def _split_text(text: str, offset: int, limit_tokens: int, para_start: bool) -> list[_Unit]:
    """Split prose into sentence units; anything still over ``limit_tokens`` into word runs."""
    units: list[_Unit] = []
    pieces: list[tuple[int, int]] = []
    pos = 0
    for m in _SENTENCE_END.finditer(text):
        pieces.append((pos, m.end()))
        pos = m.end()
    if pos < len(text):
        pieces.append((pos, len(text)))
    limit_chars = limit_tokens * 4
    for s, e in pieces:
        if approx_tokens(text[s:e]) <= limit_tokens:
            units.append(_Unit(offset + s, offset + e, approx_tokens(text[s:e]), False))
            continue
        # Pathological sentence (minified text, very long line): pack words.
        ws = s
        for w in _WORD.finditer(text, s, e):
            if w.end() - ws > limit_chars and w.start() > ws:
                units.append(
                    _Unit(
                        offset + ws, offset + w.start(), approx_tokens(text[ws : w.start()]), False
                    )
                )
                ws = w.start()
        if ws < e:
            units.append(_Unit(offset + ws, offset + e, approx_tokens(text[ws:e]), False))
    # Hard-split anything that is still too long (e.g. a single giant "word").
    out: list[_Unit] = []
    for u in units:
        while u.end - u.start > limit_chars * 2:
            cut = u.start + limit_chars
            out.append(
                _Unit(u.start, cut, approx_tokens(text[u.start - offset : cut - offset]), False)
            )
            u = _Unit(cut, u.end, approx_tokens(text[cut - offset : u.end - offset]), False)
        out.append(u)
    if out:
        out[0].para_start = para_start
    return out


def _block_units(text: str, start: int, end: int, target: int) -> list[_Unit]:
    """Units for one prose block (paragraph, list or table) spanning ``text[start:end]``."""
    block = text[start:end]
    lines = block.split("\n")
    structured = sum(1 for ln in lines if _LIST_OR_TABLE.match(ln)) >= max(1, len(lines) // 2)
    if structured and len(lines) > 1:
        units: list[_Unit] = []
        pos = start
        first = True
        for ln in lines:
            ln_end = pos + len(ln)
            if ln.strip():
                if approx_tokens(ln) > target:
                    units.extend(_split_text(ln, pos, target, first))
                else:
                    units.append(_Unit(pos, ln_end, approx_tokens(ln), first))
                first = False
            pos = ln_end + 1
        return units
    return _split_text(block, start, target, True)


def _sections(text: str, target: int) -> list[_Section]:
    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []
    current = _Section((), [])
    lines = text.split("\n")
    offsets: list[int] = []
    pos = 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1

    para: list[int] = []  # line indexes of the current prose block

    def flush_para() -> None:
        if para:
            s = offsets[para[0]]
            e = offsets[para[-1]] + len(lines[para[-1]])
            current.units.extend(_block_units(text, s, e, target))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        fm = _FENCE.match(line)
        if fm:
            flush_para()
            marker = fm.group(1)
            j = i + 1
            while j < len(lines):
                cm = _FENCE.match(lines[j])
                if (
                    cm
                    and cm.group(1)[0] == marker[0]
                    and len(cm.group(1)) >= len(marker)
                    and not lines[j].strip().lstrip(marker[0])
                ):
                    break
                j += 1
            j = min(j, len(lines) - 1)  # unterminated fence runs to the end of the document
            s, e = offsets[i], offsets[j] + len(lines[j])
            current.units.append(_Unit(s, e, approx_tokens(text[s:e]), True, code=True))
            i = j + 1
            continue
        hm = _HEADING.match(line)
        if hm:
            flush_para()
            if current.units:
                sections.append(current)
            level, title = len(hm.group(1)), _clean_heading(hm.group(2))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            current = _Section(tuple(t for _, t in stack if t), [])
            s = offsets[i]
            current.units.append(_Unit(s, s + len(line), approx_tokens(line), True, heading=True))
            i += 1
            continue
        if line.strip():
            para.append(i)
        else:
            flush_para()
        i += 1
    flush_para()
    if current.units:
        sections.append(current)
    return sections


def _split_code(text: str, unit: _Unit, hard_limit: int) -> list[tuple[int, int, str]]:
    """Split a giant fenced block on line boundaries, re-fencing each piece."""
    body = text[unit.start : unit.end]
    lines = body.split("\n")
    opener = lines[0].strip()
    m = _FENCE.match(lines[0])
    fence = m.group(1) if m else "```"
    closed = len(lines) > 1 and _FENCE.match(lines[-1]) is not None
    inner = lines[1:-1] if closed else lines[1:]
    pieces: list[tuple[int, int, str]] = []
    pos = unit.start + len(lines[0]) + 1
    buf: list[str] = []
    buf_start = pos
    limit = hard_limit * 4
    for ln in inner:
        if buf and sum(len(b) + 1 for b in buf) + len(ln) > limit:
            content = "\n".join([opener, *buf, fence])
            pieces.append((buf_start, pos - 1, content))
            buf, buf_start = [], pos
        buf.append(ln)
        pos += len(ln) + 1
    if buf:
        pieces.append((buf_start, min(pos - 1, unit.end), "\n".join([opener, *buf, fence])))
    return pieces


def chunk_markdown(
    text: str,
    *,
    target_tokens: int = 384,
    overlap_tokens: int = 48,
    max_tokens: int | None = None,
) -> list[Chunk]:
    """Split Markdown into retrieval chunks. See the module docstring for the rules."""
    target = max(16, target_tokens)
    overlap = max(0, min(overlap_tokens, target // 2))
    hard = max_tokens or max(target * 4, 1024)
    chunks: list[Chunk] = []

    def emit(path: tuple[str, ...], start: int, end: int, content: str | None = None) -> None:
        body = text[start:end] if content is None else content
        stripped = body.strip()
        if not stripped:
            return
        if content is None:  # keep offsets aligned with the stripped slice
            start += len(body) - len(body.lstrip())
            end = start + len(stripped)
        chunks.append(
            Chunk(len(chunks), " > ".join(path), stripped, approx_tokens(stripped), start, end)
        )

    for section in _sections(text, target):
        if all(u.heading for u in section.units):
            continue  # a heading with no body: its title lives on in descendants' breadcrumbs
        path = section.path
        units = section.units
        i = 0
        while i < len(units):
            u = units[i]
            if u.code and u.tokens > hard:
                for s, e, content in _split_code(text, u, hard):
                    emit(path, s, e, content)
                i += 1
                continue
            # Greedily pack units [i, j).
            j, total = i, 0
            while j < len(units):
                nxt = units[j]
                if nxt.code and nxt.tokens > hard:
                    break
                if j > i and total + nxt.tokens > target:
                    break
                total += nxt.tokens
                j += 1
            # Prefer ending on a paragraph boundary when one exists in the back half.
            if j < len(units) and not units[j].para_start:
                acc = 0
                best = None
                for k in range(i + 1, j):
                    acc += units[k - 1].tokens
                    if units[k].para_start and acc >= target // 2:
                        best = k
                if best is not None:
                    j = best
            emit(path, units[i].start, units[j - 1].end)
            if j >= len(units):
                break
            # Overlap: step back over trailing prose units worth <= overlap tokens.
            k, acc = j, 0
            while overlap and k - 1 > i:
                prev = units[k - 1]
                if prev.code or prev.heading or acc + prev.tokens > overlap:
                    break
                acc += prev.tokens
                k -= 1
            i = k
    if not chunks and text.strip():
        emit((), 0, len(text))
    return chunks
