import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState, errorMessage } from "@/components/ui/States";
import { api } from "@/lib/api/endpoints";
import { collectionsQuery, keys } from "@/lib/api/queries";
import type { Collection } from "@/lib/api/types";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import { NewCollectionDialog } from "./NewCollectionDialog";

export function CollectionsPanel({
  active,
  onSelect,
}: {
  active: string | undefined;
  onSelect: (slug: string | undefined) => void;
}) {
  const cols = useQuery(collectionsQuery());
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Collection | null>(null);
  const total = (cols.data ?? []).reduce((n, c) => n + c.document_count, 0);

  return (
    <nav aria-label="Collections" className="lg:sticky lg:top-24 lg:self-start">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="label">Collections</h2>
        <button
          type="button"
          onClick={() => setCreating(true)}
          aria-label="New collection"
          className="rounded-sm p-1 text-muted-foreground hover:bg-surface hover:text-foreground"
        >
          <Plus className="size-3.5" strokeWidth={1.75} />
        </button>
      </div>
      {cols.isError ? (
        <ErrorState error={cols.error} onRetry={() => void cols.refetch()} />
      ) : cols.isPending ? (
        <div className="space-y-2">
          <Skeleton className="h-7 w-full" />
          <Skeleton className="h-7 w-4/5" />
        </div>
      ) : (
        <ul className="-mx-2 flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
          <Row
            label="All documents"
            count={total}
            active={!active}
            onClick={() => onSelect(undefined)}
          />
          {cols.data.map((c) => (
            <Row
              key={c.id}
              label={c.name}
              count={c.document_count}
              active={active === c.slug}
              onClick={() => onSelect(c.slug)}
              onDelete={c.slug === "default" ? undefined : () => setDeleting(c)}
            />
          ))}
        </ul>
      )}
      <NewCollectionDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(c) => onSelect(c.slug)}
      />
      <DeleteCollectionDialog
        collection={deleting}
        onClose={() => setDeleting(null)}
        onDeleted={(slug) => active === slug && onSelect(undefined)}
      />
    </nav>
  );
}

function Row({
  label,
  count,
  active,
  onClick,
  onDelete,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
  onDelete?: (() => void) | undefined;
}) {
  return (
    <li className="group relative shrink-0">
      <button
        type="button"
        onClick={onClick}
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex w-full items-baseline justify-between gap-3 rounded-md px-2 py-1.5 text-left text-[13.5px] transition-colors",
          active ? "bg-surface text-foreground" : "text-muted-foreground hover:text-foreground",
        )}
      >
        <span className={cn("truncate", active && "font-medium")}>
          {active ? <span className="mr-1.5 text-accent">•</span> : null}
          {label}
        </span>
        <span className="font-mono text-[11px] tabular-nums text-faint group-hover:opacity-0 group-focus-within:opacity-0">
          {formatNumber(count)}
        </span>
      </button>
      {onDelete ? (
        <button
          type="button"
          onClick={onDelete}
          aria-label={`Delete collection ${label}`}
          className="absolute top-1/2 right-1.5 hidden -translate-y-1/2 rounded-sm p-1 text-faint opacity-0 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100 lg:block"
        >
          <Trash2 className="size-3.5" strokeWidth={1.5} />
        </button>
      ) : null}
    </li>
  );
}

function DeleteCollectionDialog({
  collection,
  onClose,
  onDeleted,
}: {
  collection: Collection | null;
  onClose: () => void;
  onDeleted: (slug: string) => void;
}) {
  const qc = useQueryClient();
  const del = useMutation({
    mutationFn: (slug: string) => api.deleteCollection(slug),
    onMutate: async (slug) => {
      await qc.cancelQueries({ queryKey: keys.collections });
      const prev = qc.getQueryData<Collection[]>(keys.collections);
      qc.setQueryData<Collection[]>(keys.collections, (cs) => cs?.filter((c) => c.slug !== slug));
      return { prev };
    },
    onError: (err, _slug, ctx) => {
      if (ctx?.prev) qc.setQueryData(keys.collections, ctx.prev);
      toast.error("Couldn’t delete collection", { description: errorMessage(err) });
    },
    onSuccess: (_d, slug) => {
      toast.success("Collection deleted");
      onDeleted(slug);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: keys.collections });
      void qc.invalidateQueries({ queryKey: keys.documents });
    },
  });
  return (
    <Dialog
      open={!!collection}
      onOpenChange={(o) => !o && onClose()}
      title="Delete collection?"
      description={
        collection ? (
          <>
            “{collection.name}” and its {collection.document_count} document
            {collection.document_count === 1 ? "" : "s"} will be removed permanently.
          </>
        ) : null
      }
      className="w-[min(460px,calc(100vw-1.5rem))]"
    >
      <div className="flex justify-end gap-2 px-6 py-4">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="danger"
          onClick={() => {
            if (collection) del.mutate(collection.slug);
            onClose();
          }}
        >
          Delete
        </Button>
      </div>
    </Dialog>
  );
}
