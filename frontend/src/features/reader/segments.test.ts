import { describe, expect, it } from "vitest";
import type { Chunk } from "@/lib/api/types";
import { segmentBody } from "./segments";

const chunk = (id: number, ord: number, content: string): Chunk => ({
  id,
  ord,
  content,
  heading_path: "",
  token_count: 10,
});

const body = [
  "# Guide",
  "",
  "Intro paragraph one. Second sentence here.",
  "",
  "## Install",
  "",
  "```sh",
  "brew install kb",
  "",
  "kb serve",
  "```",
  "",
  "Final words.",
].join("\n");

describe("segmentBody", () => {
  it("covers the body exactly once with segments anchored at chunk starts", () => {
    const chunks = [
      chunk(1, 0, "# Guide\n\nIntro paragraph one. Second sentence here."),
      // overlaps the previous chunk; its own text starts at the heading
      chunk(2, 1, "Second sentence here.\n\n## Install"),
      chunk(3, 2, "kb serve\n```\n\nFinal words."),
    ];
    const { segments, unmatched } = segmentBody(body, chunks);
    expect(unmatched).toEqual([]);
    expect(segments.map((s) => s.text).join("")).toBe(body);
    expect(segments.map((s) => s.chunkIds)).toEqual([[1], [2], [3]]);
    expect(segments[1]?.text.startsWith("## Install")).toBe(true);
    // chunk 3 starts inside a code fence, so its segment starts at the fence
    const third = segments.find((s) => s.chunkIds.includes(3));
    expect(third?.text.startsWith("```sh")).toBe(true);
  });

  it("reports chunks it cannot locate and falls back to one segment", () => {
    const { segments, unmatched } = segmentBody("plain body", [chunk(9, 0, "elsewhere")]);
    expect(unmatched).toEqual([9]);
    expect(segments).toHaveLength(1);
    expect(segments[0]?.text).toBe("plain body");
  });
});

it("shares a segment when a chunk's own text begins inside the previous block", () => {
  const text = "Para one is long. It has two sentences.\n\nNext.";
  const { segments } = segmentBody(text, [
    chunk(1, 0, "Para one is long."),
    chunk(2, 1, "It has two sentences.\n\nNext."),
  ]);
  expect(segments[0]?.chunkIds).toEqual([1, 2]);
  expect(segments.map((s) => s.text).join("")).toBe(text);
});
