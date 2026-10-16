import itertools

from kb.ingest.chunking import approx_tokens, chunk_markdown, extract_title


def _check_offsets(text: str, chunks) -> None:
    for c in chunks:
        assert text[c.char_start : c.char_end] == c.content
    assert [c.ord for c in chunks] == list(range(len(chunks)))


def test_heading_paths_and_breadcrumbs():
    text = (
        "# Guide\n\nIntro text.\n\n## Install\n\nGeneral steps.\n\n### macOS\n\nUse brew.\n\n"
        "### Linux\n\nUse apt.\n\n## Usage\n\nRun it.\n"
    )
    chunks = chunk_markdown(text, target_tokens=200, overlap_tokens=0)
    paths = [c.heading_path for c in chunks]
    assert paths == [
        "Guide",
        "Guide > Install",
        "Guide > Install > macOS",
        "Guide > Install > Linux",
        "Guide > Usage",
    ]
    assert chunks[2].content == "### macOS\n\nUse brew."
    _check_offsets(text, chunks)


def test_heading_only_sections_are_skipped_and_preamble_kept():
    text = "Preamble line.\n\n# Title\n\n## Empty\n\n## Full\n\nBody.\n"
    chunks = chunk_markdown(text)
    assert [c.heading_path for c in chunks] == ["", "Title > Full"]


def test_code_fence_never_split_and_headings_inside_ignored():
    code = "```python\n" + "\n\n".join(f"# not a heading {i}\nx = {i}" for i in range(40)) + "\n```"
    text = f"# Doc\n\nBefore.\n\n{code}\n\nAfter.\n"
    chunks = chunk_markdown(text, target_tokens=64, overlap_tokens=8)
    assert all(c.heading_path == "Doc" for c in chunks)
    holders = [c for c in chunks if "```python" in c.content]
    assert len(holders) == 1 and code in holders[0].content
    for c in chunks:
        assert c.content.count("```") % 2 == 0
    _check_offsets(text, chunks)


def test_giant_code_block_is_refenced_on_split():
    code = "```sql\n" + "\n".join(f"select {i};" for i in range(2000)) + "\n```"
    chunks = chunk_markdown(
        f"# Big\n\n{code}\n", target_tokens=64, overlap_tokens=0, max_tokens=256
    )
    code_chunks = [c for c in chunks if "select" in c.content]
    assert len(code_chunks) > 5
    for c in code_chunks:
        assert c.content.startswith("```sql\n") and c.content.endswith("\n```")
        assert c.token_count <= 300


def test_packing_respects_target_and_overlap():
    sentences = " ".join(f"Sentence number {i} talks about topic {i}." for i in range(200))
    text = f"# T\n\n{sentences}\n"
    chunks = chunk_markdown(text, target_tokens=100, overlap_tokens=20)
    assert len(chunks) > 5
    for c in chunks:
        assert c.token_count <= 110
    for a, b in itertools.pairwise(chunks):
        assert b.char_start < a.char_end, "consecutive chunks should overlap"
        assert a.char_end - b.char_start <= 20 * 4 + 8
        assert b.char_start > a.char_start
    _check_offsets(text, chunks)


def test_no_overlap_when_disabled_and_full_coverage():
    paras = "\n\n".join(f"Paragraph {i}. " + "word " * 50 for i in range(20))
    chunks = chunk_markdown(paras, target_tokens=120, overlap_tokens=0)
    for a, b in itertools.pairwise(chunks):
        assert b.char_start >= a.char_end
    joined = "".join(c.content for c in chunks)
    assert joined.count("Paragraph") == 20


def test_prefers_paragraph_boundaries():
    paras = "\n\n".join("Short sentence here. " * 6 for _ in range(12))
    chunks = chunk_markdown(paras, target_tokens=100, overlap_tokens=0)
    for c in chunks[:-1]:
        end = c.char_end
        assert paras[end:].lstrip(" ").startswith("\n\n")


def test_long_paragraph_without_punctuation_is_split():
    text = "word " * 5000
    chunks = chunk_markdown(text, target_tokens=100, overlap_tokens=10)
    assert len(chunks) > 30
    assert max(c.token_count for c in chunks) <= 110


def test_unbroken_giant_token_is_hard_split():
    text = "x" * 20_000
    chunks = chunk_markdown(text, target_tokens=100, overlap_tokens=0)
    assert len(chunks) >= 10
    assert sum(len(c.content) for c in chunks) == 20_000


def test_lists_split_by_item():
    items = "\n".join(f"- item {i} " + "detail " * 10 for i in range(60))
    chunks = chunk_markdown(f"# L\n\n{items}\n", target_tokens=80, overlap_tokens=0)
    for c in chunks[1:]:
        assert c.content.startswith("- item")


def test_deterministic():
    text = "# A\n\n" + "Lorem ipsum dolor sit amet. " * 300 + "\n\n## B\n\n```\ncode\n```\n"
    assert chunk_markdown(text) == chunk_markdown(text)


def test_empty_and_whitespace():
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n  ") == []
    one = chunk_markdown("hello")
    assert len(one) == 1 and one[0].content == "hello"


def test_unterminated_fence_runs_to_end():
    text = "# X\n\n```\ncode\n\n# still code\n"
    chunks = chunk_markdown(text)
    assert len(chunks) == 1 and "# still code" in chunks[0].content


def test_extract_title():
    assert extract_title("```\n# not this\n```\n# Real *Title*\n") == "Real Title"
    assert extract_title("## Only h2\ntext", "notes/my-file.md") == "my-file"
    assert extract_title("first line here\nmore") == "first line here"
    assert extract_title("") == "Untitled"


def test_approx_tokens():
    assert approx_tokens("") == 0
    assert approx_tokens("abcd") == 1
    assert approx_tokens("a" * 400) == 100
