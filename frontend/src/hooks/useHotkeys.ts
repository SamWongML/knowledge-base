import { useEffect, useRef } from "react";
import { isTypingTarget } from "@/lib/utils";

export interface Hotkey {
  /** e.g. "j", "/", "mod+k", "shift+?" , "Escape" */
  combo: string;
  handler: (e: KeyboardEvent) => void;
  /** Fire even while focus is in an input/textarea. */
  allowInInputs?: boolean;
}

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

export function matchesCombo(e: KeyboardEvent, combo: string): boolean {
  const parts = combo.toLowerCase().split("+");
  const key = parts.pop() ?? "";
  const mods = new Set(parts);
  const mod = isMac ? e.metaKey : e.ctrlKey;
  if (mods.has("mod") !== mod) return false;
  if (!mods.has("mod") && (e.metaKey || e.ctrlKey)) return false;
  if (mods.has("alt") !== e.altKey) return false;
  if (mods.has("shift") && !e.shiftKey) return false;
  return e.key.toLowerCase() === key;
}

/** Register global keyboard shortcuts for the lifetime of the component. */
export function useHotkeys(hotkeys: Hotkey[], enabled = true): void {
  const ref = useRef(hotkeys);
  ref.current = hotkeys;
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing) return;
      for (const h of ref.current) {
        if (!matchesCombo(e, h.combo)) continue;
        if (!h.allowInInputs && isTypingTarget(e.target)) continue;
        h.handler(e);
        return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}

export const modKeyLabel = isMac ? "⌘" : "Ctrl";
