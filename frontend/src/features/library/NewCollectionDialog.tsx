import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { FieldLabel, Input } from "@/components/ui/Field";
import { errorMessage } from "@/components/ui/States";
import { api } from "@/lib/api/endpoints";
import { keys } from "@/lib/api/queries";
import type { Collection } from "@/lib/api/types";

export function slugify(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);
}

const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;

export function NewCollectionDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onCreated: (c: Collection) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState("");
  const effectiveSlug = slugTouched ? slug : slugify(name);
  const valid = name.trim().length > 0 && SLUG.test(effectiveSlug);

  const create = useMutation({
    mutationFn: () => api.createCollection({ slug: effectiveSlug, name: name.trim(), description }),
    onSuccess: (c) => {
      qc.setQueryData<Collection[]>(keys.collections, (cs) => (cs ? [...cs, c] : [c]));
      toast.success(`Collection “${c.name}” created`);
      onCreated(c);
      onOpenChange(false);
      setName("");
      setSlug("");
      setSlugTouched(false);
      setDescription("");
    },
    onError: (err) => toast.error("Couldn’t create collection", { description: errorMessage(err) }),
  });

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="New collection"
      description="A shelf for related documents. Search can be scoped to it."
      className="w-[min(500px,calc(100vw-1.5rem))]"
    >
      <form
        className="space-y-4 px-6 py-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) create.mutate();
        }}
      >
        <FieldLabel label="Name">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Research notes"
            required
          />
        </FieldLabel>
        <FieldLabel label="Slug" hint="Lowercase letters, digits and dashes.">
          <Input
            value={effectiveSlug}
            onChange={(e) => {
              setSlugTouched(true);
              setSlug(e.target.value);
            }}
            className="font-mono text-[12.5px]"
            aria-invalid={!!effectiveSlug && !SLUG.test(effectiveSlug)}
          />
        </FieldLabel>
        <FieldLabel label="Description">
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional"
          />
        </FieldLabel>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={!valid || create.isPending}>
            Create
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
