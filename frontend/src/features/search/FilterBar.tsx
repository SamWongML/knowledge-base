import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { useState } from "react";
import { Segmented } from "@/components/ui/Segmented";
import { Tag } from "@/components/ui/StatusChip";
import { collectionsQuery } from "@/lib/api/queries";
import type { SearchMode } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const MODES = [
  { value: "hybrid", label: "Hybrid", hint: "BM25 + vectors, fused and reranked" },
  { value: "lexical", label: "Lexical", hint: "Exact terms (BM25)" },
  { value: "semantic", label: "Semantic", hint: "Meaning (dense vectors)" },
] as const satisfies readonly { value: SearchMode; label: string; hint: string }[];

export function FilterBar({
  mode,
  onMode,
  collections,
  onCollections,
  tags,
  onTags,
  inspector,
  onInspector,
}: {
  mode: SearchMode;
  onMode: (m: SearchMode) => void;
  collections: string[];
  onCollections: (c: string[]) => void;
  tags: string[];
  onTags: (t: string[]) => void;
  inspector: boolean;
  onInspector: (v: boolean) => void;
}) {
  const cols = useQuery(collectionsQuery());
  const [tagDraft, setTagDraft] = useState("");

  const toggleCollection = (slug: string) =>
    onCollections(
      collections.includes(slug) ? collections.filter((c) => c !== slug) : [...collections, slug],
    );

  const addTag = () => {
    const t = tagDraft.trim().replace(/^#/, "");
    if (t && !tags.includes(t)) onTags([...tags, t]);
    setTagDraft("");
  };

  return (
    <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-3">
      <Segmented label="Search mode" value={mode} onChange={onMode} options={MODES} />

      {cols.data && cols.data.length > 1 ? (
        <fieldset className="flex flex-wrap items-center gap-1.5">
          <legend className="sr-only">Collections</legend>
          <span className="label mr-1">in</span>
          {cols.data.map((c) => {
            const active = collections.includes(c.slug);
            return (
              <button
                key={c.id}
                type="button"
                aria-pressed={active}
                onClick={() => toggleCollection(c.slug)}
                className={cn(
                  "rounded-full border px-2.5 py-[2px] text-[12px] transition-colors",
                  active
                    ? "border-foreground bg-foreground text-background"
                    : "border-border text-muted-foreground hover:border-border-strong hover:text-foreground",
                )}
              >
                {c.name}
              </button>
            );
          })}
        </fieldset>
      ) : null}

      <div className="flex flex-wrap items-center gap-1.5">
        {tags.map((t) => (
          <Tag key={t} onRemove={() => onTags(tags.filter((x) => x !== t))}>
            {t}
          </Tag>
        ))}
        <input
          value={tagDraft}
          onChange={(e) => setTagDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              addTag();
            } else if (e.key === "Backspace" && !tagDraft && tags.length) {
              onTags(tags.slice(0, -1));
            }
          }}
          onBlur={addTag}
          placeholder="+ tag"
          aria-label="Filter by tag"
          className="w-20 bg-transparent font-mono text-[11.5px] text-muted-foreground placeholder:text-faint focus:w-28 focus:outline-none transition-[width]"
        />
      </div>

      <button
        type="button"
        aria-pressed={inspector}
        onClick={() => onInspector(!inspector)}
        className={cn(
          "ml-auto inline-flex items-center gap-1.5 label transition-colors hover:text-foreground",
          inspector && "text-accent hover:text-accent",
        )}
        title="Toggle score inspector (i)"
      >
        <Gauge className="size-3.5" strokeWidth={1.5} />
        Inspector
      </button>
    </div>
  );
}
