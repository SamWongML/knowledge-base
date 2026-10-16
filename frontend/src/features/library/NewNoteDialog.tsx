import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { lazy, Suspense, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input, Select, Textarea } from "@/components/ui/Field";
import { Kbd } from "@/components/ui/Kbd";
import { Segmented } from "@/components/ui/Segmented";
import { Skeleton } from "@/components/ui/Skeleton";
import { errorMessage } from "@/components/ui/States";
import { modKeyLabel } from "@/hooks/useHotkeys";
import { api } from "@/lib/api/endpoints";
import { collectionsQuery, keys } from "@/lib/api/queries";
import { setUI, useUI } from "@/lib/ui-store";

const Markdown = lazy(() =>
  import("@/components/markdown/Markdown").then((m) => ({ default: m.Markdown })),
);

export function NewNoteDialog() {
  const open = useUI((s) => s.newNoteOpen);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const cols = useQuery({ ...collectionsQuery(), enabled: open });
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [collection, setCollection] = useState("default");
  const [tags, setTags] = useState("");
  const [view, setView] = useState<"write" | "preview">("write");
  const titleRef = useRef<HTMLInputElement>(null);

  const close = () => setUI({ newNoteOpen: false });
  const reset = () => {
    setTitle("");
    setContent("");
    setTags("");
    setView("write");
  };

  const create = useMutation({
    mutationFn: () =>
      api.createDocument({
        title: title.trim() || null,
        content,
        collection,
        mime_type: "text/markdown",
        tags: tags
          .split(",")
          .map((t) => t.trim().replace(/^#/, ""))
          .filter(Boolean),
      }),
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: keys.documents });
      void qc.invalidateQueries({ queryKey: keys.stats });
      toast.success(res.duplicate ? "Already in the library" : "Note saved — indexing", {
        action: {
          label: "Open",
          onClick: () => void navigate({ to: "/documents/$id", params: { id: res.id } }),
        },
      });
      reset();
      close();
    },
    onError: (err) => toast.error("Couldn’t save the note", { description: errorMessage(err) }),
  });

  const canSave = content.trim().length > 0 && !create.isPending;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => setUI({ newNoteOpen: o })}
      title="New note"
      description="Markdown. Headings become the outline; it's searchable as soon as it's indexed."
      className="w-[min(760px,calc(100vw-1.5rem))]"
      initialFocus={titleRef}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (canSave) create.mutate();
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && canSave) {
            e.preventDefault();
            create.mutate();
          }
        }}
        className="px-6 py-5"
      >
        <input
          ref={titleRef}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Untitled"
          aria-label="Title"
          className="display mb-4 w-full bg-transparent text-[34px] placeholder:text-faint/70 focus:outline-none"
        />
        <div className="mb-2 flex items-center justify-between">
          <Segmented
            label="Editor view"
            value={view}
            onChange={setView}
            options={[
              { value: "write", label: "Write" },
              { value: "preview", label: "Preview" },
            ]}
          />
          <span className="label tabular-nums">
            {content.trim() ? content.trim().split(/\s+/).length : 0} words
          </span>
        </div>
        {view === "write" ? (
          <Textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={14}
            aria-label="Note content (Markdown)"
            placeholder={"# Heading\n\nWrite in Markdown…"}
            className="min-h-[280px] resize-y font-mono text-[13px] leading-[1.7]"
          />
        ) : (
          <div className="scrollbar-thin min-h-[280px] max-h-[50vh] overflow-y-auto rounded-md border border-border bg-background/60 px-5 py-4">
            {content.trim() ? (
              <Suspense fallback={<Skeleton className="h-24 w-full" />}>
                <Markdown compact>{content}</Markdown>
              </Suspense>
            ) : (
              <p className="font-serif italic text-muted-foreground">Nothing to preview yet.</p>
            )}
          </div>
        )}
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <Select
            value={collection}
            onChange={(e) => setCollection(e.target.value)}
            aria-label="Collection"
          >
            {(cols.data ?? [{ id: "default", slug: "default", name: "Default" }]).map((c) => (
              <option key={c.id} value={c.slug}>
                {c.name}
              </option>
            ))}
          </Select>
          <Input
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="tags, comma separated"
            aria-label="Tags"
            className="font-mono text-[12.5px]"
          />
        </div>
        <div className="mt-5 flex items-center justify-between gap-2">
          <span className="hidden items-center gap-1 label sm:flex">
            <Kbd>{modKeyLabel}</Kbd>
            <Kbd>↵</Kbd> save
          </span>
          <div className="ml-auto flex gap-2">
            <Button variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={!canSave}>
              {create.isPending ? "Saving…" : "Save note"}
            </Button>
          </div>
        </div>
      </form>
    </Dialog>
  );
}
