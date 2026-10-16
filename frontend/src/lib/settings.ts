import { useSyncExternalStore } from "react";
import type { Engine, SearchMode } from "./api/types";

export type ThemePreference = "light" | "dark" | "system";

export interface Settings {
  apiToken: string;
  defaultEngine: Engine;
  defaultMode: SearchMode;
  theme: ThemePreference;
  showInspector: boolean;
}

export const SETTINGS_KEY = "kb.settings";

export const DEFAULT_SETTINGS: Settings = {
  apiToken: "",
  defaultEngine: "local",
  defaultMode: "hybrid",
  theme: "system",
  showInspector: false,
};

const listeners = new Set<() => void>();
let cache: Settings | null = null;

function read(): Settings {
  try {
    const raw = globalThis.localStorage?.getItem(SETTINGS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    return { ...DEFAULT_SETTINGS, ...(isRecord(parsed) ? parsed : {}) } as Settings;
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function getSettings(): Settings {
  cache ??= read();
  return cache;
}

export function updateSettings(patch: Partial<Settings>): void {
  cache = { ...getSettings(), ...patch };
  try {
    globalThis.localStorage?.setItem(SETTINGS_KEY, JSON.stringify(cache));
  } catch {
    // Storage may be unavailable (private mode); keep the in-memory copy.
  }
  for (const l of listeners) l();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key === SETTINGS_KEY) {
      cache = read();
      listener();
    }
  };
  globalThis.addEventListener?.("storage", onStorage);
  return () => {
    listeners.delete(listener);
    globalThis.removeEventListener?.("storage", onStorage);
  };
}

export function useSettings(): Settings {
  return useSyncExternalStore(subscribe, getSettings, getSettings);
}

/** Test helper: forget the cached settings so the next read hits storage. */
export function resetSettingsCache(): void {
  cache = null;
}
