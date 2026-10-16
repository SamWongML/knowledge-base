import type { DocumentStatus } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const styles: Record<DocumentStatus, { dot: string; text: string; label: string }> = {
  pending: { dot: "bg-faint", text: "text-muted-foreground", label: "Pending" },
  processing: { dot: "bg-warning animate-pulse-ring", text: "text-warning", label: "Indexing" },
  ready: { dot: "bg-success", text: "text-success", label: "Ready" },
  failed: { dot: "bg-destructive", text: "text-destructive", label: "Failed" },
};

export function StatusChip({ status, className }: { status: DocumentStatus; className?: string }) {
  const s = styles[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 font-mono text-[10.5px] uppercase tracking-[0.08em]",
        s.text,
        className,
      )}
    >
      <span aria-hidden className={cn("size-1.5 rounded-full", s.dot)} />
      {s.label}
    </span>
  );
}

export function Tag({
  children,
  onRemove,
  active,
  onClick,
  className,
}: {
  children: string;
  onRemove?: () => void;
  active?: boolean;
  onClick?: () => void;
  className?: string;
}) {
  const base = cn(
    "inline-flex items-center gap-1 rounded-full border px-2 py-[1px] font-mono text-[11px] transition-colors",
    active
      ? "border-accent bg-accent-soft text-accent"
      : "border-border text-muted-foreground hover:border-border-strong hover:text-foreground",
    className,
  );
  const body = (
    <>
      <span aria-hidden className="opacity-50">
        #
      </span>
      {children}
    </>
  );
  if (onRemove)
    return (
      <span className={base}>
        {body}
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove tag ${children}`}
          className="-mr-1 ml-0.5 rounded-full px-1 text-faint hover:text-destructive"
        >
          ×
        </button>
      </span>
    );
  if (onClick)
    return (
      <button type="button" onClick={onClick} aria-pressed={active} className={base}>
        {body}
      </button>
    );
  return <span className={base}>{body}</span>;
}
