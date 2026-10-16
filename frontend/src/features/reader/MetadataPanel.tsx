import type { ReactNode } from "react";
import type { Document, JsonValue } from "@/lib/api/types";
import { formatDate, mimeLabel } from "@/lib/format";

function show(v: JsonValue): string {
  return typeof v === "string" ? v : JSON.stringify(v);
}

export function MetadataPanel({ doc }: { doc: Document }) {
  const rows: [string, string | ReactNode][] = [
    ["Collection", doc.collection],
    ["Format", mimeLabel(doc.mime_type)],
    ["Created", formatDate(doc.created_at)],
    ["Updated", formatDate(doc.updated_at)],
  ];
  if (doc.source_uri) {
    const isUrl = /^https?:\/\//.test(doc.source_uri);
    rows.push([
      "Source",
      isUrl ? (
        <a
          key="src"
          href={doc.source_uri}
          target="_blank"
          rel="noreferrer noopener"
          className="break-all text-accent hover:underline"
        >
          {doc.source_uri}
        </a>
      ) : (
        <span key="src" className="break-all">
          {doc.source_uri}
        </span>
      ),
    ]);
  }
  for (const [k, v] of Object.entries(doc.metadata ?? {}).slice(0, 12)) rows.push([k, show(v)]);

  return (
    <section aria-labelledby="meta-h">
      <h2 id="meta-h" className="label mb-2 border-b border-border pb-2">
        Details
      </h2>
      <dl className="grid grid-cols-[5.5rem_1fr] gap-x-3 gap-y-1.5 text-[12.5px]">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="truncate font-mono text-[10.5px] uppercase tracking-[0.06em] text-faint">
              {k}
            </dt>
            <dd className="min-w-0 break-words text-muted-foreground">{v}</dd>
          </div>
        ))}
        <dt className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-faint">ID</dt>
        <dd className="truncate font-mono text-[10.5px] text-faint" title={doc.id}>
          {doc.id}
        </dd>
      </dl>
    </section>
  );
}
