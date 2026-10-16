import { infiniteQueryOptions, keepPreviousData, queryOptions } from "@tanstack/react-query";
import { api, type DocumentListParams } from "./endpoints";
import type { SearchRequest } from "./types";

export const PAGE_SIZE = 100;

export const keys = {
  health: ["health"] as const,
  stats: ["stats"] as const,
  recent: ["queries", "recent"] as const,
  collections: ["collections"] as const,
  documents: ["documents"] as const,
  documentList: (p: Omit<DocumentListParams, "limit" | "offset">) =>
    ["documents", "list", p] as const,
  document: (id: string) => ["document", id] as const,
  chunks: (id: string) => ["document", id, "chunks"] as const,
  search: (req: SearchRequest) => ["search", req] as const,
};

export const healthQuery = () =>
  queryOptions({
    queryKey: keys.health,
    queryFn: ({ signal }) => api.health(signal),
    refetchInterval: 30_000,
  });

export const statsQuery = () =>
  queryOptions({ queryKey: keys.stats, queryFn: ({ signal }) => api.stats(signal) });

export const recentQueriesQuery = () =>
  queryOptions({ queryKey: keys.recent, queryFn: ({ signal }) => api.recentQueries(20, signal) });

export const collectionsQuery = () =>
  queryOptions({
    queryKey: keys.collections,
    queryFn: ({ signal }) => api.collections(signal),
    staleTime: 60_000,
  });

export const documentListQuery = (p: Omit<DocumentListParams, "limit" | "offset">) =>
  infiniteQueryOptions({
    queryKey: keys.documentList(p),
    queryFn: ({ pageParam, signal }) =>
      api.documents({ ...p, limit: PAGE_SIZE, offset: pageParam }, signal),
    initialPageParam: 0,
    getNextPageParam: (last) =>
      last.offset + last.items.length < last.total ? last.offset + last.items.length : undefined,
    placeholderData: keepPreviousData,
  });

export const documentQuery = (id: string) =>
  queryOptions({ queryKey: keys.document(id), queryFn: ({ signal }) => api.document(id, signal) });

export const chunksQuery = (id: string) =>
  queryOptions({ queryKey: keys.chunks(id), queryFn: ({ signal }) => api.chunks(id, signal) });

export const searchQuery = (req: SearchRequest) =>
  queryOptions({
    queryKey: keys.search(req),
    queryFn: ({ signal }) => api.search(req, signal),
    enabled: req.query.trim().length > 0,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
