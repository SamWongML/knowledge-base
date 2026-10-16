import { useSyncExternalStore } from "react";

/** Tiny global UI state for overlays that can be opened from anywhere (⌘K, shortcuts). */
interface UIState {
  paletteOpen: boolean;
  newNoteOpen: boolean;
  shortcutsOpen: boolean;
  /** Set to ask the library page's upload zone to open its file picker. */
  uploadRequested: boolean;
}

let state: UIState = {
  paletteOpen: false,
  newNoteOpen: false,
  shortcutsOpen: false,
  uploadRequested: false,
};
const listeners = new Set<() => void>();

export function setUI(patch: Partial<UIState> | ((s: UIState) => Partial<UIState>)): void {
  state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
  for (const l of listeners) l();
}

export function useUI<T>(select: (s: UIState) => T): T {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => select(state),
    () => select(state),
  );
}
