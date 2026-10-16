import json

import pytest

from kb.ingest.parsers import (
    ParseError,
    content_hash,
    html_to_markdown,
    normalize_text,
    parse_bytes,
    parse_text,
)


def test_normalize_and_hash_are_stable():
    a = "﻿Hello\r\nworld   \r\n\r\n\r\n\r\nend\n"
    b = "Hello\nworld\n\nend"
    assert normalize_text(a) == b
    assert content_hash(normalize_text(a)) == content_hash(b)


def test_markdown_passthrough_and_title():
    doc = parse_text("# My Note\n\nBody text.")
    assert doc.markdown == "# My Note\n\nBody text."
    assert doc.title == "My Note"
    assert doc.mime_type == "text/markdown"


def test_html_conversion():
    html = """<html><head><title>Page T</title><style>p{}</style><script>bad()</script></head>
    <body><nav>Menu</nav><h1>Main <em>Head</em></h1><p>Para with <a href="https://x.dev">a link</a>
    and <code>inline()</code>.</p><h2>List</h2><ul><li>one</li><li>two<ol><li>sub</li></ol></li></ul>
    <pre><code class="language-python">def f():
    return 1</code></pre><table><tr><th>k</th><th>v</th></tr><tr><td>a</td><td>1</td></tr></table>
    </body></html>"""
    md, title = html_to_markdown(html)
    assert title == "Page T"
    assert "# Main *Head*" in md
    assert "[a link](https://x.dev)" in md and "`inline()`" in md
    assert "## List" in md and "- one" in md and "  1. sub" in md
    assert "```python\ndef f():\n    return 1\n```" in md
    assert "| k | v |" in md and "| a | 1 |" in md
    assert "bad()" not in md and "p{}" not in md
    parsed = parse_bytes(html.encode(), filename="page.html")
    assert parsed.title == "Main Head" and parsed.mime_type == "text/html"


def test_json_becomes_code_block():
    parsed = parse_bytes(json.dumps({"a": [1, 2]}).encode(), filename="data.json")
    assert parsed.markdown.startswith('```json\n{\n  "a": [')
    assert parsed.title == "data"


def test_source_code_is_fenced():
    parsed = parse_bytes(b"def main():\n    pass\n", filename="tool.py")
    assert parsed.markdown == "```python\ndef main():\n    pass\n```"
    assert parsed.title == "tool"


def test_plain_text_and_encodings():
    assert parse_bytes("Grüße".encode("utf-16"), filename="a.txt").markdown == "Grüße"
    assert parse_bytes("café".encode("cp1252"), filename="b.txt").markdown == "café"


def test_errors():
    with pytest.raises(ParseError):
        parse_text("   \n ")
    with pytest.raises(ParseError):
        parse_bytes(b"\x00\x01\x02binary\xff\xfe" * 10, filename="blob.bin")
    with pytest.raises(ParseError):
        parse_text("x", mime_type="application/pdf")


def test_office_formats_without_markitdown_raise_clear_error():
    try:
        import markitdown  # noqa: F401
    except ImportError:
        with pytest.raises(ParseError, match="extra parsing"):
            parse_bytes(b"%PDF-1.7 ...", filename="paper.pdf")
    else:
        pytest.skip("markitdown is installed")
