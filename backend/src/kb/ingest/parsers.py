"""Turn uploaded bytes or submitted text into normalised Markdown.

Built-in (no dependencies): Markdown / plain text / source code, HTML and JSON.
PDF, DOCX, PPTX and XLSX go through Microsoft MarkItDown when the optional ``parsing``
extra is installed (``uv sync --extra parsing``); otherwise :class:`ParseError` explains
how to enable it and the document is marked failed.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath

from kb.ingest.chunking import extract_title, find_h1


class ParseError(ValueError):
    """The input cannot be converted to text. Permanent: retrying will not help."""


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    markdown: str
    title: str
    mime_type: str


_MARKITDOWN_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_MARKITDOWN_MIME = {v: k for k, v in _MARKITDOWN_EXT.items()}
_HTML_EXT = {".html", ".htm", ".xhtml"}
_JSON_EXT = {".json", ".jsonl", ".ndjson", ".geojson"}
_TEXT_EXT = {".md", ".markdown", ".mdx", ".txt", ".text", ".rst", ".org", ".adoc", ""}
_CODE_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".swift": "swift",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby",
    ".php": "php", ".sh": "bash", ".zsh": "bash", ".sql": "sql", ".yaml": "yaml",
    ".yml": "yaml", ".toml": "toml", ".ini": "ini", ".css": "css", ".scss": "scss",
    ".xml": "xml", ".csv": "csv", ".tsv": "tsv", ".lua": "lua", ".r": "r", ".scala": "scala",
}  # fmt: skip


def normalize_text(text: str) -> str:
    """Canonical text form used for storage and the dedup hash."""
    text = unicodedata.normalize("NFC", text.removeprefix("﻿"))
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _kind(filename: str | None, mime_type: str | None) -> tuple[str, str]:
    """Return (kind, extension) where kind is markdown | html | json | code | markitdown."""
    ext = _ext(filename)
    mime = (mime_type or "").split(";")[0].strip().lower()
    if ext in _MARKITDOWN_EXT or mime in _MARKITDOWN_MIME:
        return "markitdown", ext or _MARKITDOWN_MIME[mime]
    if ext in _HTML_EXT or mime in ("text/html", "application/xhtml+xml"):
        return "html", ext
    if ext in _JSON_EXT or mime == "application/json" or mime.endswith("+json"):
        return "json", ext
    if ext in _CODE_LANG:
        return "code", ext
    return "markdown", ext


def _decode(data: bytes) -> str:
    for enc in (
        ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",)
    ):
        with contextlib.suppress(UnicodeDecodeError):
            return data.decode(enc)
    if b"\x00" in data[:4096]:
        raise ParseError("binary file of unsupported type")
    return data.decode("cp1252", errors="replace")


def _ext(filename: str | None) -> str:
    return PurePosixPath((filename or "").replace("\\", "/")).suffix.lower()


def _convert(
    text: str, kind: str, ext: str, filename: str | None, mime_type: str
) -> ParsedDocument:
    html_title: str | None = None
    if kind == "html":
        text, html_title = html_to_markdown(text)
    elif kind == "json":
        text = json_to_markdown(text)
    elif kind == "code":
        fence = "````" if "```" in text else "```"
        text = f"{fence}{_CODE_LANG[ext]}\n{text.rstrip()}\n{fence}"
    markdown = normalize_text(text)
    if not markdown:
        raise ParseError("document has no text content")
    title = find_h1(markdown) or html_title or extract_title(markdown, filename)
    return ParsedDocument(markdown, title, mime_type)


def parse_text(
    text: str, *, mime_type: str | None = "text/markdown", filename: str | None = None
) -> ParsedDocument:
    """Normalise text submitted through the API.

    The declared MIME type decides the format (Markdown by default; HTML and JSON are
    converted). ``text/plain`` with a source-code file name is wrapped in a code fence.
    ``filename`` otherwise only serves as the title fallback.
    """
    mime = mime_type or "text/markdown"
    kind, _ = _kind(None, mime)
    ext = _ext(filename)
    if kind == "markitdown":
        raise ParseError(f"{mime} content must be uploaded as a file")
    if kind == "markdown" and mime.startswith(("text/plain", "text/x-")) and ext in _CODE_LANG:
        kind = "code"
    return _convert(text, kind, ext, filename, mime)


def parse_bytes(data: bytes, *, filename: str, mime_type: str | None = None) -> ParsedDocument:
    """Convert an uploaded file (format from the extension, then the MIME type).

    CPU-bound for binary formats: call it via ``asyncio.to_thread``.
    """
    kind, ext = _kind(filename, mime_type)
    if kind == "markitdown":
        markdown = _markitdown(data, ext)
        if not markdown:
            raise ParseError(f"no text could be extracted from {filename}")
        return ParsedDocument(
            markdown, extract_title(markdown, filename), mime_type or _MARKITDOWN_EXT[ext]
        )
    default_mime = {"html": "text/html", "json": "application/json", "code": "text/plain"}
    mime = mime_type or default_mime.get(kind, "text/markdown")
    return _convert(_decode(data), kind, ext, filename, mime)


def _markitdown(data: bytes, ext: str) -> str:
    try:
        from markitdown import MarkItDown, StreamInfo  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ParseError(
            f"{ext or 'binary'} files need the optional parser: install with "
            "`uv sync --extra parsing` (markitdown[pdf,docx,pptx,xlsx])"
        ) from exc
    try:
        result = MarkItDown(enable_plugins=False).convert_stream(
            io.BytesIO(data), stream_info=StreamInfo(extension=ext)
        )
    except Exception as exc:  # markitdown raises a zoo of converter-specific errors
        raise ParseError(f"could not parse {ext} file: {exc}") from exc
    return normalize_text(result.markdown or "")


def json_to_markdown(text: str) -> str:
    try:
        pretty = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except ValueError:
        pretty = text  # JSON Lines or slightly broken JSON: keep verbatim
    return f"```json\n{pretty}\n```"


# --- HTML ---------------------------------------------------------------------------

_SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe", "object", "canvas"}
_BLOCK = {
    "p", "div", "section", "article", "main", "header", "footer", "aside", "nav", "form",
    "figure", "figcaption", "details", "summary", "dl", "dt", "dd", "address", "hr", "center",
}  # fmt: skip
_VOID = {
    "br",
    "hr",
    "img",
    "input",
    "meta",
    "link",
    "area",
    "base",
    "col",
    "embed",
    "source",
    "wbr",
}


class _HTMLToMarkdown(HTMLParser):
    """Small, forgiving HTML→Markdown converter keeping headings, lists, code, links, tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.title: str | None = None
        self._skip = 0
        self._in_title = False
        self._title_buf: list[str] = []
        self._pre = 0
        self._lists: list[list[int | str]] = []  # [kind, counter]
        self._links: list[str | None] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._table_rows = 0
        self._quote = 0

    # helpers
    def _write(self, s: str) -> None:
        if self._cell is not None:
            self._cell.append(s)
        else:
            self.out.append(s)

    def _block(self) -> None:
        if self._cell is not None:
            self._cell.append(" ")
            return
        self.out.append("\n\n" + "> " * self._quote)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
            return
        if tag in _SKIP:
            if tag not in _VOID:
                self._skip += 1
            return
        if self._skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._block()
            self._write("#" * int(tag[1]) + " ")
        elif tag in _BLOCK:
            self._block()
            if tag == "hr":
                self._write("---")
                self._block()
        elif tag == "br":
            self._write("\n" if self._pre else "  \n" + "> " * self._quote)
        elif tag == "blockquote":
            self._quote += 1
            self._block()
        elif tag == "pre":
            self._block()
            lang = ""
            cls = a.get("class") or ""
            m = re.search(r"(?:language|lang)-([\w+-]+)", cls)
            if m:
                lang = m.group(1)
            self._write(f"```{lang}\n")
            self._pre += 1
        elif tag == "code" and not self._pre:
            self._write("`")
        elif tag == "code" and self.out and self.out[-1] == "```\n":
            # <pre><code class="language-x">: move the language onto the fence.
            m = re.search(r"(?:language|lang)-([\w+-]+)", a.get("class") or "")
            if m:
                self.out[-1] = f"```{m.group(1)}\n"
        elif tag in ("ul", "ol"):
            if not self._lists:
                self._block()
            self._lists.append([tag, 0])
        elif tag == "li":
            depth = max(len(self._lists) - 1, 0)
            marker = "-"
            if self._lists and self._lists[-1][0] == "ol":
                self._lists[-1][1] = int(self._lists[-1][1]) + 1
                marker = f"{self._lists[-1][1]}."
            self._write("\n" + "> " * self._quote + "  " * depth + marker + " ")
        elif tag in ("strong", "b"):
            self._write("**")
        elif tag in ("em", "i"):
            self._write("*")
        elif tag == "a":
            href = a.get("href")
            self._links.append(href if href and not href.startswith("javascript:") else None)
            if self._links[-1]:
                self._write("[")
        elif tag == "img":
            alt = (a.get("alt") or "").strip()
            if alt:
                self._write(f"[image: {alt}]")
        elif tag == "table":
            self._block()
            self._table_rows = 0
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            self.title = " ".join("".join(self._title_buf).split()) or None
            return
        if tag in _SKIP:
            if tag not in _VOID:
                self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") or tag in _BLOCK:
            self._block()
        elif tag == "blockquote":
            self._quote = max(0, self._quote - 1)
            self._block()
        elif tag == "pre":
            self._pre = max(0, self._pre - 1)
            self._write("\n```")
            self._block()
        elif tag == "code" and not self._pre:
            self._write("`")
        elif tag in ("ul", "ol"):
            if self._lists:
                self._lists.pop()
            if not self._lists:
                self._block()
        elif tag in ("strong", "b"):
            self._write("**")
        elif tag in ("em", "i"):
            self._write("*")
        elif tag == "a":
            href = self._links.pop() if self._links else None
            if href:
                self._write(f"]({href})")
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()).replace("|", "\\|"))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            row, self._row = self._row, None
            if row:
                self.out.append("\n| " + " | ".join(row) + " |")
                if self._table_rows == 0:
                    self.out.append("\n|" + " --- |" * len(row))
                self._table_rows += 1
        elif tag == "table":
            self._block()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_buf.append(data)
            return
        if self._skip:
            return
        if self._pre:
            self._write(data)
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip() and (not self.out or self.out[-1].endswith((" ", "\n"))):
            return
        self._write(text)


def html_to_markdown(html: str) -> tuple[str, str | None]:
    """Return ``(markdown, <title> text or None)``."""
    parser = _HTMLToMarkdown()
    parser.feed(html)
    parser.close()
    md = "".join(parser.out)
    # Tidy: trim spaces around newlines outside code, collapse blank runs.
    lines: list[str] = []
    in_code = False
    for line in md.split("\n"):
        if line.lstrip("> ").startswith("```"):
            in_code = not in_code
            lines.append(line.strip())
        elif in_code:
            lines.append(line)
        else:
            line = line[1:] if line[:1] == " " and line[1:2] not in (" ", "") else line
            lines.append(line.rstrip() if line.strip(" >") else "")
    md = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    md = re.sub(r"(?m)^[ \t]+(?=#)", "", md)
    md = re.sub(r"(?m)^(#{1,6}) +", r"\1 ", md)
    return md, parser.title
