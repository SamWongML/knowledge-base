import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UploadCloud } from "lucide-react";
import { useEffect, useState } from "react";
import { useDropzone } from "react-dropzone";
import { toast } from "sonner";
import { Select } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { errorMessage } from "@/components/ui/States";
import { api } from "@/lib/api/endpoints";
import { collectionsQuery, keys } from "@/lib/api/queries";
import { setUI, useUI } from "@/lib/ui-store";
import { cn } from "@/lib/utils";

const ACCEPT = {
  "text/markdown": [".md", ".markdown"],
  "text/plain": [".txt", ".text", ".rst", ".org"],
  "text/html": [".html", ".htm"],
  "application/pdf": [".pdf"],
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
  "application/json": [".json"],
  "text/csv": [".csv"],
};

export function UploadZone({ defaultCollection }: { defaultCollection: string }) {
  const qc = useQueryClient();
  const cols = useQuery(collectionsQuery());
  const [collection, setCollection] = useState(defaultCollection);
  const [tags, setTags] = useState("");
  useEffect(() => setCollection(defaultCollection), [defaultCollection]);

  const upload = useMutation({
    mutationFn: (files: File[]) =>
      api.upload(
        files,
        collection,
        tags
          .split(",")
          .map((t) => t.trim().replace(/^#/, ""))
          .filter(Boolean),
      ),
    onSuccess: (accepted, files) => {
      const dupes = accepted.filter((a) => a.duplicate).length;
      toast.success(`${files.length} file${files.length === 1 ? "" : "s"} queued for indexing`, {
        description: dupes
          ? `${dupes} already in the library (deduplicated)`
          : "Status updates live below.",
      });
      void qc.invalidateQueries({ queryKey: keys.documents });
      void qc.invalidateQueries({ queryKey: keys.stats });
    },
    onError: (err) => toast.error("Upload failed", { description: errorMessage(err) }),
  });

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    accept: ACCEPT,
    multiple: true,
    noClick: true,
    noKeyboard: true,
    onDropAccepted: (files) => upload.mutate(files),
    onDropRejected: (rej) =>
      toast.error(`${rej.length} file${rej.length === 1 ? "" : "s"} skipped`, {
        description: rej
          .slice(0, 3)
          .map((r) => r.file.name)
          .join(", "),
      }),
  });

  // Respond to "Upload files" from the command palette or the header button.
  const requested = useUI((s) => s.uploadRequested);
  useEffect(() => {
    if (requested) {
      setUI({ uploadRequested: false });
      open();
    }
  }, [requested, open]);

  return (
    <div
      {...getRootProps({
        className: cn(
          "relative mb-6 flex flex-wrap items-center gap-x-4 gap-y-3 rounded-lg border border-dashed px-4 py-3.5 transition-colors",
          isDragActive ? "border-accent bg-accent-soft" : "border-border-strong/70 bg-surface/40",
        ),
      })}
    >
      <input {...getInputProps()} aria-label="Upload files" />
      <span
        className={cn(
          "grid size-9 place-items-center rounded-full border",
          isDragActive ? "border-accent text-accent" : "border-border text-muted-foreground",
        )}
      >
        {upload.isPending ? <Spinner /> : <UploadCloud className="size-4" strokeWidth={1.5} />}
      </span>
      <div className="min-w-0 flex-1 text-[13px]">
        <p className="font-medium">
          {isDragActive
            ? "Release to add to the library"
            : upload.isPending
              ? "Uploading…"
              : "Drop files to ingest"}
        </p>
        <p className="text-muted-foreground">
          Markdown, text, HTML, PDF, DOCX — or{" "}
          <button
            type="button"
            onClick={open}
            className="text-accent underline-offset-4 hover:underline"
          >
            browse
          </button>
        </p>
      </div>
      <div className="flex w-full items-center gap-2 sm:w-auto">
        <Select
          aria-label="Upload into collection"
          value={collection}
          onChange={(e) => setCollection(e.target.value)}
          className="h-8 w-full text-[12.5px] sm:w-40"
        >
          {(cols.data ?? [{ id: "default", slug: "default", name: "Default" }]).map((c) => (
            <option key={c.id} value={c.slug}>
              {c.name}
            </option>
          ))}
        </Select>
        <input
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          placeholder="tags, comma separated"
          aria-label="Tags for uploaded files"
          className="h-8 w-full min-w-0 rounded-md border border-border bg-background/60 px-2.5 font-mono text-[11.5px] placeholder:text-faint focus:border-accent focus:outline-none sm:w-48"
        />
      </div>
    </div>
  );
}
