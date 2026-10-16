import type { Chunk } from "@/lib/api/types";

/**
 * Chunks are exact (overlapping) slices of the document body. To render the body once while
 * still being able to anchor and highlight each chunk, we split the body into non-overlapping
 * segments. Each chunk's segment begins where its *new* text begins (after the overlap with
 * the previous chunk), snapped back to a block boundary (blank line or heading) and never
 * inside a fenced code block. Chunks that snap into an earlier segment share that segment.
 */
export interface Segment {
  start: number;
  end: number;
  text: string;
  chunkIds: number[];
}

export interface Segmentation {
  segments: Segment[];
  /** Chunks whose text could not be located in the body. */
  unmatched: number[];
}

function fenceRanges(body: string): [number, number][] {
  const ranges: [number, number][] = [];
  const re = /^ {0,3}(`{3,}|~{3,})[^\n]*$/gm;
  let open: { start: number; fence: string } | null = null;
  for (const m of body.matchAll(re)) {
    const marker = m[1] ?? "";
    if (!open) open = { start: m.index, fence: marker };
    else if (marker[0] === open.fence[0] && marker.length >= open.fence.length) {
      ranges.push([open.start, m.index + m[0].length]);
      open = null;
    }
  }
  if (open) ranges.push([open.start, body.length]);
  return ranges;
}

function locate(body: string, content: string, from: number): number {
  if (!content) return -1;
  let i = body.indexOf(content, from);
  if (i < 0) i = body.indexOf(content);
  if (i < 0) {
    const probe = content.trim().slice(0, 120);
    if (probe.length >= 16) {
      i = body.indexOf(probe, from);
      if (i < 0) i = body.indexOf(probe);
    }
  }
  return i;
}

function snap(body: string, pos: number, fences: [number, number][]): number {
  if (pos <= 0) return 0;
  // Already at a line start that follows a blank line or begins a heading.
  const lineStart = body.lastIndexOf("\n", pos - 1) + 1;
  const blank = body.lastIndexOf("\n\n", pos - 1);
  let boundary = blank < 0 ? 0 : blank + 2;
  if (lineStart > boundary && /^ {0,3}#{1,6}\s/.test(body.slice(lineStart, lineStart + 8))) {
    boundary = lineStart;
  }
  if (boundary > pos) boundary = lineStart;
  for (const [s, e] of fences) {
    if (boundary > s && boundary < e) return s;
  }
  return boundary;
}

export function segmentBody(body: string, chunks: readonly Chunk[]): Segmentation {
  const sorted = [...chunks].sort((a, b) => a.ord - b.ord);
  const fences = fenceRanges(body);
  const starts: { pos: number; id: number }[] = [];
  const unmatched: number[] = [];
  let cursor = 0;
  let prevEnd = 0;
  for (const c of sorted) {
    const at = locate(body, c.content, cursor);
    if (at < 0) {
      unmatched.push(c.id);
      continue;
    }
    cursor = at + 1;
    // Start of this chunk's own text, skipping the overlap and leading whitespace.
    let own = Math.max(at, Math.min(prevEnd, at + c.content.length));
    while (own < body.length && /\s/.test(body[own] ?? "")) own++;
    prevEnd = Math.max(prevEnd, at + c.content.length);
    starts.push({ pos: snap(body, own, fences), id: c.id });
  }
  if (!starts.length) {
    return { segments: [{ start: 0, end: body.length, text: body, chunkIds: [] }], unmatched };
  }

  const segments: Segment[] = [];
  for (const s of starts) {
    const last = segments.at(-1);
    const pos = segments.length === 0 ? 0 : s.pos;
    if (last && pos <= last.start) {
      last.chunkIds.push(s.id);
    } else {
      segments.push({ start: pos, end: body.length, text: "", chunkIds: [s.id] });
    }
  }
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const next = segments[i + 1];
    if (!seg) continue;
    seg.end = next ? next.start : body.length;
    seg.text = body.slice(seg.start, seg.end);
  }
  return { segments, unmatched };
}
