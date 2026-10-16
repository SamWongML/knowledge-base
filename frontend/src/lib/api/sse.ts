import { createParser, type EventSourceMessage } from "eventsource-parser";

export interface SSEMessage {
  event: string;
  data: string;
  id?: string | undefined;
}

/**
 * Read a `text/event-stream` response body as an async sequence of messages.
 * Works for POST streams (which native EventSource cannot do) and honours abort signals.
 */
export async function* readSSE(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SSEMessage> {
  const queue: SSEMessage[] = [];
  const parser = createParser({
    onEvent(msg: EventSourceMessage) {
      queue.push({ event: msg.event ?? "message", data: msg.data, id: msg.id });
    },
  });
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const onAbort = () => {
    reader.cancel().catch(() => {});
  };
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    while (true) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
      while (queue.length) {
        const next = queue.shift();
        if (next) yield next;
      }
    }
    const tail = decoder.decode();
    if (tail) parser.feed(tail);
    // Flush a final event that lacked a trailing blank line.
    parser.feed("\n\n");
    while (queue.length) {
      const next = queue.shift();
      if (next) yield next;
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    reader.releaseLock();
  }
}

/** Parse JSON SSE data, returning undefined on malformed payloads instead of throwing. */
export function parseJSON<T>(data: string): T | undefined {
  try {
    return JSON.parse(data) as T;
  } catch {
    return undefined;
  }
}
