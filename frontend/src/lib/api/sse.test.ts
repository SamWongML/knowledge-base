import { describe, expect, it } from "vitest";
import { toAskEvent } from "./endpoints";
import { readSSE } from "./sse";

function streamOf(...parts: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(c) {
      for (const p of parts) c.enqueue(enc.encode(p));
      c.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>, signal?: AbortSignal) {
  const out = [];
  for await (const m of readSSE(stream, signal)) out.push(m);
  return out;
}

describe("readSSE", () => {
  it("parses named events split across arbitrary chunk boundaries", async () => {
    const msgs = await collect(
      streamOf(
        "event: st",
        'ep\ndata: {"type":"plan"',
        ',"message":"go"}\n',
        "\nevent: done\ndata: {}\n\n",
      ),
    );
    expect(msgs).toEqual([
      { event: "step", data: '{"type":"plan","message":"go"}', id: undefined },
      { event: "done", data: "{}", id: undefined },
    ]);
  });

  it("joins multi-line data, handles CRLF and ignores comments", async () => {
    const msgs = await collect(streamOf(": keep-alive\r\n\r\ndata: a\r\ndata: b\r\n\r\n"));
    expect(msgs).toEqual([{ event: "message", data: "a\nb", id: undefined }]);
  });

  it("decodes multi-byte UTF-8 split between chunks", async () => {
    const bytes = new TextEncoder().encode("data: café ✓\n\n");
    const cut = bytes.indexOf(0xc3) + 1; // split inside "é"
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, cut));
        c.enqueue(bytes.slice(cut));
        c.close();
      },
    });
    const msgs = await collect(stream);
    expect(msgs[0]?.data).toBe("café ✓");
  });

  it("flushes a trailing event without a blank line", async () => {
    const msgs = await collect(streamOf("event: ping\ndata: 1"));
    expect(msgs).toEqual([{ event: "ping", data: "1", id: undefined }]);
  });

  it("stops when aborted", async () => {
    const ctrl = new AbortController();
    const enc = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(enc.encode("data: first\n\n"));
        // never closes
      },
    });
    const out: string[] = [];
    for await (const m of readSSE(stream, ctrl.signal)) {
      out.push(m.data);
      ctrl.abort();
    }
    expect(out).toEqual(["first"]);
  });
});

describe("toAskEvent", () => {
  it("normalises steps with missing optional fields", () => {
    const [ev] = [...toAskEvent("step", '{"type":"search","query":"bm25"}')];
    expect(ev).toEqual({
      type: "step",
      step: { type: "search", message: "", query: "bm25", hits: null, data: {} },
    });
  });

  it("maps error payloads to a message", () => {
    expect([...toAskEvent("error", '{"detail":"model down"}')]).toEqual([
      { type: "error", message: "model down" },
    ]);
    expect([...toAskEvent("error", "plain failure")]).toEqual([
      { type: "error", message: "plain failure" },
    ]);
  });

  it("skips malformed JSON and unknown events", () => {
    expect([...toAskEvent("step", "{nope")]).toEqual([]);
    expect([...toAskEvent("ping", "")]).toEqual([]);
  });
});
