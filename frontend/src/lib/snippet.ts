/**
 * Safe rendering of search snippets. The server wraps matches in <mark>…</mark> and escapes
 * everything else. We never inject HTML: the string is tokenised into text runs and only
 * literal <mark>/<\/mark> tags toggle highlighting. Any other markup stays visible as text.
 */

export interface SnippetPart {
  text: string;
  marked: boolean;
}

const TAG = /<(\/?)mark>/gi;

const NAMED: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
  hellip: "…",
  mdash: "—",
  ndash: "–",
};

export function decodeEntities(input: string): string {
  return input.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (match, body: string) => {
    if (body[0] === "#") {
      const code =
        body[1] === "x" || body[1] === "X"
          ? Number.parseInt(body.slice(2), 16)
          : Number.parseInt(body.slice(1), 10);
      return Number.isFinite(code) && code > 0 && code <= 0x10ffff
        ? String.fromCodePoint(code)
        : match;
    }
    return NAMED[body.toLowerCase()] ?? match;
  });
}

export function parseSnippet(html: string): SnippetPart[] {
  const parts: SnippetPart[] = [];
  let depth = 0;
  let last = 0;
  const push = (raw: string) => {
    if (!raw) return;
    const text = decodeEntities(raw);
    const marked = depth > 0;
    const prev = parts.at(-1);
    if (prev && prev.marked === marked) prev.text += text;
    else parts.push({ text, marked });
  };
  for (const m of html.matchAll(TAG)) {
    push(html.slice(last, m.index));
    depth = m[1] ? Math.max(0, depth - 1) : depth + 1;
    last = m.index + m[0].length;
  }
  push(html.slice(last));
  return parts;
}

/** Plain text of a snippet (tags removed, entities decoded). */
export function snippetText(html: string): string {
  return parseSnippet(html)
    .map((p) => p.text)
    .join("");
}
