import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn(
        "animate-pulse rounded-sm bg-[color-mix(in_oklch,var(--foreground)_7%,transparent)]",
        className,
      )}
    />
  );
}
