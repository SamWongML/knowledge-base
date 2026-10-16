import { useMemo } from "react";
import { parseSnippet } from "@/lib/snippet";

/** Render a server snippet, honouring only <mark> highlighting. Never uses innerHTML. */
export function Snippet({ html }: { html: string }) {
  const parts = useMemo(() => parseSnippet(html), [html]);
  return (
    <>
      {parts.map((p, i) =>
        p.marked ? (
          // biome-ignore lint/suspicious/noArrayIndexKey: parts are positional and immutable
          <mark key={i}>{p.text}</mark>
        ) : (
          // biome-ignore lint/suspicious/noArrayIndexKey: parts are positional and immutable
          <span key={i}>{p.text}</span>
        ),
      )}
    </>
  );
}
