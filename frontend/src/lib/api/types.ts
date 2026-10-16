/**
 * Hand-written mirror of backend/src/kb/schemas.py (the API contract).
 * Keep field names identical. UUIDs and datetimes travel as strings.
 */

export type UUID = string;
export type ISODateTime = string;

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";
export const DOCUMENT_STATUSES: readonly DocumentStatus[] = [
  "pending",
  "processing",
  "ready",
  "failed",
];

export type SearchMode = "hybrid" | "lexical" | "semantic";
export const SEARCH_MODES: readonly SearchMode[] = ["hybrid", "lexical", "semantic"];

export type Engine = "local" | "cloud";

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

// --- collections -------------------------------------------------------------------

export interface CollectionIn {
  slug: string;
  name: string;
  description?: string;
}

export interface Collection {
  id: UUID;
  slug: string;
  name: string;
  description: string;
  created_at: ISODateTime;
  document_count: number;
}

// --- documents ---------------------------------------------------------------------

export interface DocumentIn {
  title?: string | null;
  content: string;
  collection?: string;
  source_uri?: string | null;
  mime_type?: string;
  tags?: string[];
  metadata?: JsonObject;
}

export interface DocumentPatch {
  title?: string | null;
  content?: string | null;
  tags?: string[] | null;
  metadata?: JsonObject | null;
  collection?: string | null;
}

export interface DocumentSummary {
  id: UUID;
  collection_id: UUID;
  collection: string;
  title: string;
  source_uri: string | null;
  mime_type: string;
  tags: string[];
  status: DocumentStatus;
  error: string | null;
  chunk_count: number;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface Document extends DocumentSummary {
  body: string;
  metadata: JsonObject;
}

export interface IngestAccepted {
  id: UUID;
  status: DocumentStatus;
  duplicate: boolean;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Chunk {
  id: number;
  ord: number;
  heading_path: string;
  content: string;
  token_count: number;
}

// --- search ------------------------------------------------------------------------

export interface SearchFilters {
  collections?: string[] | null;
  tags?: string[] | null;
  document_ids?: UUID[] | null;
  updated_after?: ISODateTime | null;
}

export interface SearchRequest {
  query: string;
  mode?: SearchMode;
  limit?: number;
  filters?: SearchFilters;
  rerank?: boolean;
  llm_rerank?: boolean | null;
  expand_context?: boolean;
}

export interface StageTiming {
  stage: string;
  ms: number;
  detail: string | null;
}

/** Per-stage scores. Known keys: bm25, dense, rrf, rerank, llm. */
export type StageScores = Partial<Record<"bm25" | "dense" | "rrf" | "rerank" | "llm", number>> &
  Record<string, number>;

export interface SearchHit {
  chunk_id: number;
  document_id: UUID;
  document_title: string;
  collection: string;
  source_uri: string | null;
  heading_path: string;
  content: string;
  /** Highlighted with <mark>…</mark>; everything else is escaped server-side. */
  snippet: string;
  score: number;
  scores: StageScores;
  ord: number;
  tags: string[];
}

export interface SearchResponse {
  query: string;
  mode: SearchMode;
  hits: SearchHit[];
  timings: StageTiming[];
  degraded: string[];
  total_ms: number;
}

// --- agentic answer ----------------------------------------------------------------

export interface AskRequest {
  question: string;
  filters?: SearchFilters;
  max_steps?: number | null;
  engine?: Engine;
}

export interface Citation {
  n: number;
  chunk_id: number;
  document_id: UUID;
  document_title: string;
  heading_path: string;
  snippet: string;
}

export type AgentStepType =
  | "plan"
  | "search"
  | "read"
  | "reflect"
  | "answer_delta"
  | "answer"
  | "error";

export interface AgentStep {
  type: AgentStepType;
  message: string;
  query: string | null;
  hits: SearchHit[] | null;
  data: JsonObject;
}

export interface AskResponse {
  question: string;
  answer: string;
  citations: Citation[];
  steps: AgentStep[];
  engine: string;
  total_ms: number;
}

// --- system ------------------------------------------------------------------------

export interface ComponentHealth {
  name: string;
  ok: boolean;
  latency_ms: number | null;
  detail: string | null;
}

export interface Health {
  status: "ok" | "degraded" | "down";
  components: ComponentHealth[];
  version: string;
}

export interface Stats {
  documents: number;
  chunks: number;
  collections: number;
  queued_jobs: number;
  failed_jobs: number;
  queries_24h: number;
}

export interface RecentQuery {
  query: string;
  mode: string;
  created_at: ISODateTime;
}

// --- server-sent events ------------------------------------------------------------

export interface DocumentStatusEvent {
  id: UUID;
  collection_id: UUID;
  status: DocumentStatus;
  title: string;
  error: string | null;
  chunk_count: number;
}
