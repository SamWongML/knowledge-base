import { describe, expect, it } from "vitest";
import { citedNumbers, splitCitations } from "./citations";

describe("splitCitations", () => {
  it("finds single, grouped and adjacent markers", () => {
    expect(splitCitations("A [1], B [2, 3][4].")).toEqual([
      { type: "text", value: "A " },
      { type: "cite", n: 1 },
      { type: "text", value: ", B " },
      { type: "cite", n: 2 },
      { type: "cite", n: 3 },
      { type: "cite", n: 4 },
      { type: "text", value: "." },
    ]);
  });

  it("ignores links, footnotes, reference definitions and non-numeric brackets", () => {
    const text = "see [1](http://x) and [^2] and [note] and [3]: def";
    expect(splitCitations(text)).toEqual([{ type: "text", value: text }]);
  });

  it("leaves [0] as text and keeps semicolon groups", () => {
    expect(citedNumbers("[0] x [5; 6] y [5]")).toEqual([5, 6]);
    expect(splitCitations("[0] x")).toEqual([{ type: "text", value: "[0] x" }]);
  });
});
