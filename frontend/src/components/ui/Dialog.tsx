import { Dialog as BaseDialog } from "@base-ui/react/dialog";
import { X } from "lucide-react";
import type { ReactNode, RefObject } from "react";
import { cn } from "@/lib/utils";

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  className,
  hideHeader,
  initialFocus,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
  hideHeader?: boolean;
  initialFocus?: RefObject<HTMLElement | null>;
}) {
  return (
    <BaseDialog.Root open={open} onOpenChange={(o) => onOpenChange(o)}>
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className="fixed inset-0 z-40 bg-[oklch(0.2_0.02_60/0.28)] backdrop-blur-[2px] transition-opacity duration-200 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 dark:bg-[oklch(0_0_0/0.5)]" />
        <BaseDialog.Popup
          initialFocus={initialFocus}
          className={cn(
            "shadow-paper fixed top-[12vh] left-1/2 z-50 max-h-[80vh] w-[min(640px,calc(100vw-1.5rem))] -translate-x-1/2 overflow-y-auto rounded-lg border border-border bg-surface-raised p-0 outline-none transition-[opacity,transform] duration-200 ease-out data-[ending-style]:translate-y-1 data-[ending-style]:opacity-0 data-[starting-style]:translate-y-2 data-[starting-style]:opacity-0",
            className,
          )}
        >
          {hideHeader ? (
            <BaseDialog.Title className="sr-only">{title}</BaseDialog.Title>
          ) : (
            <header className="flex items-start justify-between gap-4 border-b border-border px-6 pt-5 pb-4">
              <div>
                <BaseDialog.Title className="display text-[28px]">{title}</BaseDialog.Title>
                {description ? (
                  <BaseDialog.Description className="mt-1 text-[13px] text-muted-foreground">
                    {description}
                  </BaseDialog.Description>
                ) : null}
              </div>
              <BaseDialog.Close
                aria-label="Close"
                className="-mr-2 rounded-md p-1.5 text-muted-foreground hover:bg-surface hover:text-foreground"
              >
                <X className="size-4" strokeWidth={1.5} />
              </BaseDialog.Close>
            </header>
          )}
          {children}
        </BaseDialog.Popup>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  );
}
