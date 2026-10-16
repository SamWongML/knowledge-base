import { type ComponentProps, memo, useMemo } from "react";
import { defaultRemarkPlugins, Streamdown } from "streamdown";
import type { Citation } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { CitationContext, CitationMarker } from "./CitationMarker";
import { remarkCitations } from "./remarkCitations";

const remarkWithCitations = [...Object.values(defaultRemarkPlugins), remarkCitations];
const allowedTags = { cite: ["dataN", "data-n"] };

type CiteProps = ComponentProps<"cite"> & { "data-n"?: string | number };

function Cite(props: CiteProps) {
  const n = Number(props["data-n"]);
  return Number.isFinite(n) ? <CitationMarker n={n} /> : <cite>{props.children}</cite>;
}

const components = { cite: Cite };

export interface MarkdownProps {
  children: string;
  streaming?: boolean;
  citations?: readonly Citation[];
  className?: string;
  compact?: boolean;
}

/** Streamdown with editorial typography and optional inline citation markers. */
export const Markdown = memo(function Markdown({
  children,
  streaming = false,
  citations,
  className,
  compact,
}: MarkdownProps) {
  const map = useMemo(() => new Map((citations ?? []).map((c) => [c.n, c])), [citations]);
  const withCitations = citations !== undefined;
  return (
    <CitationContext.Provider value={map}>
      <Streamdown
        mode={streaming ? "streaming" : "static"}
        isAnimating={streaming}
        caret={streaming ? "block" : undefined}
        className={cn("prose-kb", compact && "prose-compact", className)}
        remarkPlugins={withCitations ? remarkWithCitations : undefined}
        allowedTags={withCitations ? allowedTags : undefined}
        components={withCitations ? components : undefined}
        controls={{ code: true, table: false, mermaid: false }}
        linkSafety={{ enabled: false }}
      >
        {children}
      </Streamdown>
    </CitationContext.Provider>
  );
});
