import { Link } from "@tanstack/react-router";
import { Moon, Search, Sun } from "lucide-react";
import { Kbd } from "@/components/ui/Kbd";
import { Tooltip } from "@/components/ui/Tooltip";
import { modKeyLabel } from "@/hooks/useHotkeys";
import type { LiveState } from "@/lib/api/live";
import { setUI } from "@/lib/ui-store";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Search", exact: true },
  { to: "/ask", label: "Ask", exact: false },
  { to: "/library", label: "Library", exact: false },
  { to: "/system", label: "System", exact: false },
] as const;

export function Masthead({
  live,
  theme,
  onToggleTheme,
}: {
  live: LiveState;
  theme: "light" | "dark";
  onToggleTheme: () => void;
}) {
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/80 backdrop-blur-md supports-[backdrop-filter]:bg-background/70">
      <div className="mx-auto flex h-14 w-full max-w-[1200px] items-center gap-4 px-4 sm:gap-8 sm:px-8">
        <Link
          to="/"
          className="group flex items-baseline gap-0.5 rounded-sm"
          aria-label="Commonplace — home"
        >
          <span className="display text-[26px] italic leading-none">Commonplace</span>
          <span className="display text-[30px] leading-none text-accent transition-transform duration-300 group-hover:translate-x-0.5">
            .
          </span>
        </Link>

        <nav aria-label="Primary" className="hidden h-full items-stretch gap-6 md:flex">
          {NAV.map((item) => (
            <NavLink key={item.to} {...item} />
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-1.5 sm:gap-3">
          <LiveDot state={live} />
          <button
            type="button"
            onClick={() => setUI({ paletteOpen: true })}
            className="group flex h-8 items-center gap-2 rounded-md border border-border bg-surface-raised/60 pr-1.5 pl-2.5 text-[12.5px] text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground"
            aria-label="Open command palette"
          >
            <Search className="size-3.5" strokeWidth={1.75} />
            <span className="hidden sm:inline">Jump to…</span>
            <span className="hidden items-center gap-0.5 sm:flex">
              <Kbd>{modKeyLabel}</Kbd>
              <Kbd>K</Kbd>
            </span>
          </button>
          <Tooltip content={theme === "dark" ? "Light mode (t)" : "Dark mode (t)"}>
            <button
              type="button"
              onClick={onToggleTheme}
              aria-label="Toggle colour theme"
              className="grid size-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-surface hover:text-foreground"
            >
              {theme === "dark" ? (
                <Sun className="size-4" strokeWidth={1.5} />
              ) : (
                <Moon className="size-4" strokeWidth={1.5} />
              )}
            </button>
          </Tooltip>
        </div>
      </div>
      <nav
        aria-label="Primary"
        className="scrollbar-thin flex gap-5 overflow-x-auto border-t border-border px-4 md:hidden"
      >
        {NAV.map((item) => (
          <NavLink key={item.to} {...item} />
        ))}
      </nav>
    </header>
  );
}

function NavLink({ to, label, exact }: { to: string; label: string; exact: boolean }) {
  return (
    <Link
      to={to}
      activeOptions={{ exact, includeSearch: false }}
      className="relative flex items-center py-2.5 text-[13.5px] text-muted-foreground transition-colors hover:text-foreground data-[status=active]:text-foreground"
    >
      {({ isActive }) => (
        <>
          {label}
          <span
            aria-hidden
            className={cn(
              "absolute inset-x-0 -bottom-px h-[1.5px] origin-left bg-accent transition-transform duration-300 ease-out",
              isActive ? "scale-x-100" : "scale-x-0",
            )}
          />
        </>
      )}
    </Link>
  );
}

function LiveDot({ state }: { state: LiveState }) {
  const label =
    state === "live"
      ? "Live updates connected"
      : state === "connecting"
        ? "Connecting…"
        : "Live updates offline — retrying";
  return (
    <Tooltip content={label}>
      <button
        type="button"
        aria-label={label}
        className="hidden cursor-default items-center gap-1.5 rounded-sm px-1 label sm:flex"
      >
        <span
          className={cn(
            "size-1.5 rounded-full",
            state === "live" && "bg-success",
            state === "connecting" && "animate-pulse bg-warning",
            state === "offline" && "bg-destructive",
          )}
        />
        {state === "live" ? "live" : state === "connecting" ? "…" : "offline"}
      </button>
    </Tooltip>
  );
}
