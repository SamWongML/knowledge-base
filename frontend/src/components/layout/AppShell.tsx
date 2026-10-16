import { Outlet, useNavigate } from "@tanstack/react-router";
import { LazyMotion, MotionConfig } from "motion/react";
import { useRef } from "react";
import { Toaster } from "sonner";
import { CommandPalette } from "@/components/command/CommandPalette";
import { NewNoteDialog } from "@/features/library/NewNoteDialog";
import { useHotkeys } from "@/hooks/useHotkeys";
import { useTheme } from "@/hooks/useTheme";
import { useLiveEvents } from "@/lib/api/live";
import { setUI } from "@/lib/ui-store";
import { Masthead } from "./Masthead";
import { ShortcutsDialog } from "./ShortcutsDialog";

const CHORDS: Record<string, "/" | "/ask" | "/library" | "/system"> = {
  s: "/",
  a: "/ask",
  l: "/library",
  y: "/system",
};

export function AppShell() {
  const live = useLiveEvents();
  const { resolved, toggle } = useTheme();
  const navigate = useNavigate();
  const lastG = useRef(0);

  useHotkeys([
    {
      combo: "mod+k",
      allowInInputs: true,
      handler: (e) => {
        e.preventDefault();
        setUI((s) => ({ paletteOpen: !s.paletteOpen }));
      },
    },
    { combo: "?", handler: () => setUI({ shortcutsOpen: true }) },
    {
      combo: "n",
      handler: (e) => {
        e.preventDefault();
        setUI({ newNoteOpen: true });
      },
    },
    { combo: "t", handler: () => toggle() },
    {
      combo: "g",
      handler: () => {
        lastG.current = Date.now();
      },
    },
    ...Object.entries(CHORDS).map(([key, to]) => ({
      combo: key,
      handler: () => {
        if (Date.now() - lastG.current < 900) {
          lastG.current = 0;
          void navigate({ to });
        }
      },
    })),
  ]);

  return (
    <LazyMotion features={loadMotionFeatures} strict>
      <MotionConfig reducedMotion="user">
        <div className="flex min-h-dvh flex-col">
          <a
            href="#main"
            className="sr-only z-50 rounded bg-foreground px-3 py-2 text-background focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
          >
            Skip to content
          </a>
          <Masthead live={live} theme={resolved} onToggleTheme={toggle} />
          <main
            id="main"
            className="mx-auto w-full max-w-[1200px] flex-1 px-4 pt-8 pb-24 sm:px-8 sm:pt-12"
          >
            <Outlet />
          </main>
          <Colophon />
          <CommandPalette />
          <NewNoteDialog />
          <ShortcutsDialog />
          <Toaster
            theme={resolved}
            position="bottom-right"
            toastOptions={{
              classNames: {
                toast:
                  "!rounded-md !border !border-border !bg-surface-raised !text-foreground !font-sans !shadow-paper",
                description: "!text-muted-foreground",
              },
            }}
          />
        </div>
      </MotionConfig>
    </LazyMotion>
  );
}

const loadMotionFeatures = () => import("@/lib/motion-features").then((mod) => mod.default);

function Colophon() {
  return (
    <footer className="mx-auto w-full max-w-[1200px] px-4 pb-8 sm:px-8">
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4 label">
        <span>Commonplace — a private knowledge base</span>
        <span>
          Press <span className="text-foreground">?</span> for shortcuts
        </span>
      </div>
    </footer>
  );
}
