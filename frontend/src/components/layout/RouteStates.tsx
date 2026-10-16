import { type ErrorComponentProps, Link } from "@tanstack/react-router";
import { buttonClass } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/States";

export function RoutePending() {
  return (
    <div className="space-y-4" aria-busy>
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-12 w-2/3" />
      <Skeleton className="h-4 w-1/2" />
    </div>
  );
}

export function RouteError({ error, reset }: ErrorComponentProps) {
  return (
    <div className="mx-auto max-w-xl pt-10">
      <ErrorState error={error} onRetry={reset} />
    </div>
  );
}

export function RouteNotFound() {
  return (
    <EmptyState
      glyph="¶"
      title="Nothing on this shelf"
      action={
        <Link to="/" className={buttonClass("secondary")}>
          Back to search
        </Link>
      }
    >
      The page you asked for doesn’t exist.
    </EmptyState>
  );
}
