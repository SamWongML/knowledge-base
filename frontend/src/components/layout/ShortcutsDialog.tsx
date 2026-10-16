import { Dialog } from "@/components/ui/Dialog";
import { Kbd } from "@/components/ui/Kbd";
import { modKeyLabel } from "@/hooks/useHotkeys";
import { setUI, useUI } from "@/lib/ui-store";

const GROUPS: { title: string; items: [string[], string][] }[] = [
  {
    title: "Anywhere",
    items: [
      [[modKeyLabel, "K"], "Command palette"],
      [["g", "s"], "Go to Search"],
      [["g", "a"], "Go to Ask"],
      [["g", "l"], "Go to Library"],
      [["g", "y"], "Go to System"],
      [["n"], "New note"],
      [["t"], "Toggle theme"],
      [["?"], "This sheet"],
    ],
  },
  {
    title: "Search results",
    items: [
      [["/"], "Focus the search field"],
      [["j"], "Next result"],
      [["k"], "Previous result"],
      [["↵"], "Open in reader"],
      [["i"], "Toggle score inspector"],
    ],
  },
];

export function ShortcutsDialog() {
  const open = useUI((s) => s.shortcutsOpen);
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => setUI({ shortcutsOpen: o })}
      title="Keyboard"
      description="Everything is reachable without the mouse."
      className="w-[min(560px,calc(100vw-1.5rem))]"
    >
      <div className="grid gap-8 px-6 py-5 sm:grid-cols-2">
        {GROUPS.map((g) => (
          <section key={g.title}>
            <h3 className="label mb-3">{g.title}</h3>
            <dl className="space-y-2">
              {g.items.map(([keys, label]) => (
                <div key={label} className="flex items-center justify-between gap-4 text-[13px]">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="flex gap-1">
                    {keys.map((k) => (
                      <Kbd key={k}>{k}</Kbd>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </Dialog>
  );
}
