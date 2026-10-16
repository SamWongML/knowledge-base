import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        "inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[4px] border border-border-strong/70 border-b-2 bg-surface-raised px-1 font-mono text-[10.5px] leading-none text-muted-foreground",
        className,
      )}
    >
      {children}
    </kbd>
  );
}
