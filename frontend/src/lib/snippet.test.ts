import { describe, expect, it } from "vitest";
import { decodeEntities, parseSnippet, snippetText } from "./snippet";

describe("parseSnippet", () => {
  it("splits marked and unmarked runs", () => {
    expect(parseSnippet("a <mark>b</mark> c")).toEqual([
      { text: "a ", marked: false },
      { text: "b", marked: true },
      { text: " c", marked: false },
    ]);
  });

  it("decodes entities after splitting, so escaped tags stay text", () => {
    const parts = parseSnippet("x &lt;mark&gt;y&lt;/mark&gt; &amp; <MARK>z</MARK>");
    expect(parts).toEqual([
      { text: "x <mark>y</mark> & ", marked: false },
      { text: "z", marked: true },
    ]);
  });

  it("never interprets other tags as markup", () => {
    const parts = parseSnippet(
      '<img src=x onerror="alert(1)"><mark>hit</mark><script>bad()</script>',
    );
    expect(parts.map((p) => p.text).join("")).toBe(
      '<img src=x onerror="alert(1)">hit<script>bad()</script>',
    );
    expect(parts.filter((p) => p.marked)).toEqual([{ text: "hit", marked: true }]);
  });

  it("tolerates unbalanced tags", () => {
    expect(parseSnippet("</mark>a<mark>b")).toEqual([
      { text: "a", marked: false },
      { text: "b", marked: true },
    ]);
  });

  it("merges adjacent runs with the same state", () => {
    expect(parseSnippet("<mark>a</mark><mark>b</mark>")).toEqual([{ text: "ab", marked: true }]);
  });
});

describe("decodeEntities", () => {
  it("handles numeric, hex and named entities and leaves unknown ones", () => {
    expect(decodeEntities("&#39;&#x27;&quot;&hellip;&bogus;")).toBe("''\"…&bogus;");
  });
  it("snippetText strips marks", () => {
    expect(snippetText("<mark>a</mark> &amp; b")).toBe("a & b");
  });
});
