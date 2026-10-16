import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { errorMessage } from "@/components/ui/States";
import { Tag } from "@/components/ui/StatusChip";
import { api } from "@/lib/api/endpoints";
import { keys } from "@/lib/api/queries";
import type { Document } from "@/lib/api/types";

/** Inline tag editing with optimistic updates (rolled back on failure). */
export function TagEditor({ doc }: { doc: Document }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");

  const save = useMutation({
    mutationFn: (tags: string[]) => api.updateDocument(doc.id, { tags }),
    onMutate: async (tags) => {
      await qc.cancelQueries({ queryKey: keys.document(doc.id) });
      const prev = qc.getQueryData<Document>(keys.document(doc.id));
      qc.setQueryData<Document>(keys.document(doc.id), (d) => (d ? { ...d, tags } : d));
      return { prev };
    },
    onError: (err, _tags, ctx) => {
      if (ctx?.prev) qc.setQueryData(keys.document(doc.id), ctx.prev);
      toast.error("Couldn’t update tags", { description: errorMessage(err) });
    },
    onSuccess: (updated) => qc.setQueryData(keys.document(doc.id), updated),
    onSettled: () => void qc.invalidateQueries({ queryKey: keys.documents }),
  });

  const add = () => {
    const t = draft.trim().replace(/^#/, "").toLowerCase();
    setDraft("");
    if (t && !doc.tags.includes(t)) save.mutate([...doc.tags, t]);
  };

  return (
    <section aria-labelledby="tags-h">
      <h2 id="tags-h" className="label mb-2 border-b border-border pb-2">
        Tags
      </h2>
      <div className="flex flex-wrap items-center gap-1.5">
        {doc.tags.map((t) => (
          <Tag key={t} onRemove={() => save.mutate(doc.tags.filter((x) => x !== t))}>
            {t}
          </Tag>
        ))}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add();
            }
          }}
          onBlur={add}
          placeholder="+ add tag"
          aria-label="Add tag"
          className="min-w-0 flex-1 bg-transparent py-0.5 font-mono text-[11.5px] placeholder:text-faint focus:outline-none"
        />
      </div>
    </section>
  );
}
