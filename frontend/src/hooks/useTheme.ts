import { useEffect, useSyncExternalStore } from "react";
import { type ThemePreference, updateSettings, useSettings } from "@/lib/settings";

const query = "(prefers-color-scheme: dark)";

function subscribeSystem(cb: () => void) {
  const mql = window.matchMedia?.(query);
  mql?.addEventListener("change", cb);
  return () => mql?.removeEventListener("change", cb);
}
const systemDark = () => window.matchMedia?.(query).matches ?? false;

/** Resolves the theme preference and keeps the `.dark` class on <html> in sync. */
export function useTheme(): {
  preference: ThemePreference;
  resolved: "light" | "dark";
  setPreference: (t: ThemePreference) => void;
  toggle: () => void;
} {
  const { theme } = useSettings();
  const sysDark = useSyncExternalStore(subscribeSystem, systemDark, () => false);
  const resolved = theme === "system" ? (sysDark ? "dark" : "light") : theme;

  useEffect(() => {
    const root = document.documentElement;
    const apply = () => root.classList.toggle("dark", resolved === "dark");
    const doc = document as Document & { startViewTransition?: (cb: () => void) => unknown };
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (
      doc.startViewTransition &&
      !reduce &&
      root.classList.contains("dark") !== (resolved === "dark")
    ) {
      doc.startViewTransition(apply);
    } else apply();
  }, [resolved]);

  return {
    preference: theme,
    resolved,
    setPreference: (t) => updateSettings({ theme: t }),
    toggle: () => updateSettings({ theme: resolved === "dark" ? "light" : "dark" }),
  };
}
