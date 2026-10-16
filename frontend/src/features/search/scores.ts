import type { SearchHit } from "@/lib/api/types";

/** Display order and labels for per-stage scores. */
export const STAGES = [
  { key: "bm25", label: "BM25" },
  { key: "dense", label: "Dense" },
  { key: "rrf", label: "RRF" },
  { key: "rerank", label: "Rerank" },
  { key: "llm", label: "LLM" },
] as const;

/** Largest absolute score per stage across a result set, used to scale bars. */
export function stageMaxima(hits: SearchHit[]): Record<string, number> {
  const max: Record<string, number> = {};
  for (const h of hits) {
    for (const [k, v] of Object.entries(h.scores)) {
      if (Number.isFinite(v)) max[k] = Math.max(max[k] ?? 0, Math.abs(v));
    }
  }
  return max;
}

export function stageEntries(
  scores: SearchHit["scores"],
): { key: string; label: string; value: number }[] {
  const known: { key: string; label: string; value: number }[] = [];
  for (const s of STAGES) {
    const v = scores[s.key];
    if (typeof v === "number") known.push({ key: s.key, label: s.label, value: v });
  }
  for (const [k, v] of Object.entries(scores)) {
    if (!STAGES.some((s) => s.key === k) && typeof v === "number")
      known.push({ key: k, label: k, value: v });
  }
  return known;
}
