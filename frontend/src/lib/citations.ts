/**
 * Citation markers in answers look like `[1]`, `[2, 3]` or `[1][4]`.
 * Markdown links (`[1](url)`) and footnotes (`[^1]`) are not citations.
 */

export type CitationPart = { type: "text"; value: string } | { type: "cite"; n: number };

const MARKER = /\[(\d{1,3}(?:\s*[,;]\s*\d{1,3})*)\](?!\(|:)/g;

export function splitCitations(text: string): CitationPart[] {
  const out: CitationPart[] = [];
  let last = 0;
  for (const m of text.matchAll(MARKER)) {
    const nums = (m[1] ?? "")
      .split(/[,;]/)
      .map((n) => Number.parseInt(n.trim(), 10))
      .filter((n) => Number.isFinite(n) && n > 0);
    if (!nums.length) continue;
    if (m.index > last) pushText(out, text.slice(last, m.index));
    for (const n of nums) out.push({ type: "cite", n });
    last = m.index + m[0].length;
  }
  if (last < text.length) pushText(out, text.slice(last));
  return out;
}

function pushText(out: CitationPart[], value: string): void {
  const prev = out.at(-1);
  if (prev?.type === "text") prev.value += value;
  else out.push({ type: "text", value });
}

/** Distinct citation numbers in order of first appearance. */
export function citedNumbers(text: string): number[] {
  const seen = new Set<number>();
  for (const p of splitCitations(text)) if (p.type === "cite") seen.add(p.n);
  return [...seen];
}
