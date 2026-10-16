import { Dialog as BaseDialog } from "@base-ui/react/dialog";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Command } from "cmdk";
import {
  BookOpen,
  CornerDownLeft,
  FilePlus2,
  FileText,
  Gauge,
  type LucideIcon,
  MessageSquareText,
  Moon,
  Search,
  Upload,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { Kbd } from "@/components/ui/Kbd";
import { Spinner } from "@/components/ui/Spinner";
import { useDebounced } from "@/hooks/useDebounced";
import { useTheme } from "@/hooks/useTheme";
import { searchQuery } from "@/lib/api/queries";
import { splitHeadingPath } from "@/lib/format";
import { snippetText } from "@/lib/snippet";
import { setUI, useUI } from "@/lib/ui-store";

interface Action {
  id: string;
  label: string;
  icon: LucideIcon;
  hint?: string;
  run: () => void;
}

export function CommandPalette() {
  const open = useUI((s) => s.paletteOpen);
  const [input, setInput] = useState("");
  const query = useDebounced(input.trim(), 180);
  const navigate = useNavigate();
  const { toggle } = useTheme();

  const close = () => {
    setUI({ paletteOpen: false });
    setInput("");
  };
  const go = (fn: () => void) => () => {
    close();
    fn();
  };

  const results = useQuery({
    ...searchQuery({ query, mode: "hybrid", limit: 8, rerank: false, llm_rerank: false }),
    enabled: open && query.length > 1,
  });

  const actions: Action[] = [
    {
      id: "search",
      label: query ? `Search for “${query}”` : "Search",
      icon: Search,
      hint: "g s",
      run: () => void navigate({ to: "/", search: query ? { q: query } : {} }),
    },
    {
      id: "ask",
      label: query ? `Ask: “${query}”` : "Ask a question",
      icon: MessageSquareText,
      hint: "g a",
      run: () => void navigate({ to: "/ask", search: query ? { q: query } : {} }),
    },
    {
      id: "library",
      label: "Open library",
      icon: BookOpen,
      hint: "g l",
      run: () => void navigate({ to: "/library" }),
    },
    {
      id: "system",
      label: "System & settings",
      icon: Gauge,
      hint: "g y",
      run: () => void navigate({ to: "/system" }),
    },
    {
      id: "note",
      label: "New note",
      icon: FilePlus2,
      hint: "n",
      run: () => setUI({ newNoteOpen: true }),
    },
    {
      id: "upload",
      label: "Upload files",
      icon: Upload,
      run: () => {
        void navigate({ to: "/library" });
        setUI({ uploadRequested: true });
      },
    },
    { id: "theme", label: "Toggle theme", icon: Moon, hint: "t", run: toggle },
  ];
  const needle = input.trim().toLowerCase();
  const visibleActions =
    needle && !["search", "ask"].some((k) => k.startsWith(needle))
      ? actions.filter(
          (a) => a.id === "search" || a.id === "ask" || a.label.toLowerCase().includes(needle),
        )
      : actions;

  const hits = results.data?.hits ?? [];

  return (
    <BaseDialog.Root open={open} onOpenChange={(o) => (o ? setUI({ paletteOpen: true }) : close())}>
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className="fixed inset-0 z-40 bg-[oklch(0.2_0.02_60/0.25)] backdrop-blur-[2px] transition-opacity duration-150 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 dark:bg-[oklch(0_0_0/0.5)]" />
        <BaseDialog.Popup className="shadow-paper fixed top-[14vh] left-1/2 z-50 w-[min(640px,calc(100vw-1.5rem))] -translate-x-1/2 overflow-hidden rounded-lg border border-border bg-surface-raised outline-none transition-[opacity,transform] duration-150 data-[ending-style]:scale-[0.98] data-[ending-style]:opacity-0 data-[starting-style]:scale-[0.98] data-[starting-style]:opacity-0">
          <BaseDialog.Title className="sr-only">Command palette</BaseDialog.Title>
          <Command shouldFilter={false} loop label="Command palette">
            <div className="flex items-center gap-3 border-b border-border px-4">
              <Search className="size-4 text-muted-foreground" strokeWidth={1.5} />
              <Command.Input
                autoFocus
                value={input}
                onValueChange={setInput}
                placeholder="Search documents, jump, or run an action…"
                className="h-14 flex-1 bg-transparent font-serif text-[19px] placeholder:text-faint focus:outline-none"
              />
              {results.isFetching ? <Spinner className="text-muted-foreground" /> : <Kbd>esc</Kbd>}
            </div>
            <Command.List className="scrollbar-thin max-h-[min(60vh,460px)] overflow-y-auto p-2">
              <Command.Empty className="px-3 py-8 text-center text-[13px] text-muted-foreground">
                Nothing matches.
              </Command.Empty>
              {hits.length > 0 && (
                <Group heading="Passages">
                  {hits.map((h) => (
                    <Item
                      key={`${h.chunk_id}`}
                      value={`hit-${h.chunk_id}`}
                      onSelect={go(
                        () =>
                          void navigate({
                            to: "/documents/$id",
                            params: { id: h.document_id },
                            hash: `chunk-${h.chunk_id}`,
                          }),
                      )}
                    >
                      <FileText
                        className="mt-0.5 size-4 shrink-0 text-muted-foreground"
                        strokeWidth={1.5}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline gap-2">
                          <span className="truncate font-medium">{h.document_title}</span>
                          <span className="truncate font-mono text-[10.5px] text-faint">
                            {splitHeadingPath(h.heading_path).join(" › ")}
                          </span>
                        </div>
                        <p className="mt-0.5 line-clamp-1 font-serif text-[14px] text-muted-foreground">
                          {snippetText(h.snippet)}
                        </p>
                      </div>
                    </Item>
                  ))}
                </Group>
              )}
              <Group heading="Go & do">
                {visibleActions.map((a) => (
                  <Item key={a.id} value={a.id} onSelect={go(a.run)}>
                    <a.icon className="size-4 shrink-0 text-muted-foreground" strokeWidth={1.5} />
                    <span className="flex-1">{a.label}</span>
                    {a.hint ? (
                      <span className="flex gap-1">
                        {a.hint.split(" ").map((k) => (
                          <Kbd key={k}>{k}</Kbd>
                        ))}
                      </span>
                    ) : null}
                  </Item>
                ))}
              </Group>
            </Command.List>
            <div className="flex items-center justify-between border-t border-border bg-surface/60 px-4 py-2 label">
              <span className="flex items-center gap-1.5">
                <Kbd>↑</Kbd>
                <Kbd>↓</Kbd> move
                <span className="mx-2 opacity-40">·</span>
                <Kbd>
                  <CornerDownLeft className="size-2.5" />
                </Kbd>{" "}
                open
              </span>
              <span>Commonplace</span>
            </div>
          </Command>
        </BaseDialog.Popup>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  );
}

function Group({ heading, children }: { heading: string; children: ReactNode }) {
  return (
    <Command.Group
      heading={heading}
      className="mb-1 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1.5 [&_[cmdk-group-heading]]:font-mono [&_[cmdk-group-heading]]:text-[10.5px] [&_[cmdk-group-heading]]:tracking-[0.08em] [&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group-heading]]:uppercase"
    >
      {children}
    </Command.Group>
  );
}

function Item({
  value,
  onSelect,
  children,
}: {
  value: string;
  onSelect: () => void;
  children: ReactNode;
}) {
  return (
    <Command.Item
      value={value}
      onSelect={onSelect}
      className="flex cursor-pointer items-start gap-3 rounded-md px-3 py-2 text-[13.5px] data-[selected=true]:bg-accent-soft data-[selected=true]:[&_svg]:text-accent"
    >
      {children}
    </Command.Item>
  );
}
