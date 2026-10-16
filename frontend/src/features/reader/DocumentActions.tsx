import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { errorMessage } from "@/components/ui/States";
import { api } from "@/lib/api/endpoints";
import { keys } from "@/lib/api/queries";
import type { Document } from "@/lib/api/types";

export function DocumentActions({ doc }: { doc: Document }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [confirm, setConfirm] = useState(false);

  const reindex = useMutation({
    mutationFn: () => api.reindexDocument(doc.id),
    onMutate: () => {
      const prev = qc.getQueryData<Document>(keys.document(doc.id));
      qc.setQueryData<Document>(keys.document(doc.id), (d) =>
        d ? { ...d, status: "pending", error: null } : d,
      );
      return { prev };
    },
    onError: (err, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(keys.document(doc.id), ctx.prev);
      toast.error("Couldn’t reindex", { description: errorMessage(err) });
    },
    onSuccess: () => toast.success("Reindexing queued"),
  });

  const remove = useMutation({
    mutationFn: () => api.deleteDocument(doc.id),
    onSuccess: () => {
      qc.removeQueries({ queryKey: keys.document(doc.id) });
      void qc.invalidateQueries({ queryKey: keys.documents });
      void qc.invalidateQueries({ queryKey: keys.stats });
      void qc.invalidateQueries({ queryKey: keys.collections });
      toast.success(`Deleted “${doc.title}”`);
      void navigate({ to: "/library" });
    },
    onError: (err) => toast.error("Couldn’t delete", { description: errorMessage(err) }),
  });

  const busy = doc.status === "pending" || doc.status === "processing";

  return (
    <section className="flex flex-wrap gap-2 border-t border-border pt-4">
      <Button size="sm" onClick={() => reindex.mutate()} disabled={busy || reindex.isPending}>
        <RefreshCw strokeWidth={1.5} className={busy ? "animate-spin" : undefined} />
        {busy ? "Indexing…" : "Reindex"}
      </Button>
      <Button
        size="sm"
        variant="danger"
        onClick={() => setConfirm(true)}
        disabled={remove.isPending}
      >
        <Trash2 strokeWidth={1.5} /> Delete
      </Button>
      <Dialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Delete document?"
        description={
          <>
            “{doc.title}” and its {doc.chunk_count} passages will be removed. This cannot be undone.
          </>
        }
        className="w-[min(460px,calc(100vw-1.5rem))]"
      >
        <div className="flex justify-end gap-2 px-6 py-4">
          <Button variant="ghost" onClick={() => setConfirm(false)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              setConfirm(false);
              remove.mutate();
            }}
          >
            Delete
          </Button>
        </div>
      </Dialog>
    </section>
  );
}
