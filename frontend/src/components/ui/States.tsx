import { AlertTriangle, RotateCw } from "lucide-react";
import type { ReactNode } from "react";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { Button } from "./Button";

export function EmptyState({
  title,
  children,
  action,
  glyph = "※",
  className,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  glyph?: string;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center py-16 text-center animate-fade-in", className)}>
      <div aria-hidden className="display mb-3 text-5xl text-accent/70">
        {glyph}
      </div>
      <h3 className="display text-2xl">{title}</h3>
      {children ? (
        <div className="mt-2 max-w-sm text-[13.5px] text-muted-foreground">{children}</div>
      ) : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401)
      return "The server rejected the API token. Set it under System → Settings.";
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-3 rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-[13.5px]",
        className,
      )}
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" strokeWidth={1.75} />
      <div className="min-w-0 flex-1">
        <p className="font-medium text-foreground">Couldn’t load this.</p>
        <p className="mt-0.5 break-words text-muted-foreground">{errorMessage(error)}</p>
      </div>
      {onRetry ? (
        <Button size="sm" variant="ghost" onClick={onRetry}>
          <RotateCw strokeWidth={1.75} /> Retry
        </Button>
      ) : null}
    </div>
  );
}
