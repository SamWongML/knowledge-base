const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 31_536_000],
  ["month", 2_592_000],
  ["week", 604_800],
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
];

export function timeAgo(iso: string, now: number = Date.now()): string {
  const seconds = (new Date(iso).getTime() - now) / 1000;
  if (!Number.isFinite(seconds)) return "";
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en", { year: "numeric", month: "short", day: "numeric" });
}

export function formatMs(ms: number): string {
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

export function formatNumber(n: number): string {
  return new Intl.NumberFormat("en").format(n);
}

export function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

/** Split a heading path like "Guide > Setup > Docker" into crumbs. */
export function splitHeadingPath(path: string): string[] {
  return path
    .split(/\s*(?:>|›|»|\/)\s*/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function mimeLabel(mime: string): string {
  const map: Record<string, string> = {
    "text/markdown": "md",
    "text/plain": "txt",
    "text/html": "html",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
  };
  return map[mime] ?? mime.split("/").pop() ?? mime;
}
