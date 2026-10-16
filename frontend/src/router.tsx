import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
  type RouterHistory,
} from "@tanstack/react-router";
import { AppShell } from "@/components/layout/AppShell";
import { RouteError, RouteNotFound, RoutePending } from "@/components/layout/RouteStates";
import { SearchPage } from "@/features/search/SearchPage";
import {
  DOCUMENT_STATUSES,
  type DocumentStatus,
  SEARCH_MODES,
  type SearchMode,
} from "@/lib/api/types";

export interface RouterContext {
  queryClient: QueryClient;
}

// --- search-param validation ------------------------------------------------------

const str = (v: unknown): string | undefined =>
  typeof v === "string" && v.trim() ? v : typeof v === "number" ? String(v) : undefined;

const strList = (v: unknown): string[] | undefined => {
  const list = Array.isArray(v) ? v : typeof v === "string" ? v.split(",") : [];
  const out = list.map((x) => String(x).trim()).filter(Boolean);
  return out.length ? out : undefined;
};

export interface SearchParams {
  q?: string | undefined;
  mode?: SearchMode | undefined;
  collections?: string[] | undefined;
  tags?: string[] | undefined;
}

export interface LibraryParams {
  collection?: string | undefined;
  status?: DocumentStatus | undefined;
  tag?: string | undefined;
  q?: string | undefined;
}

// --- routes -------------------------------------------------------------------------

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: AppShell,
  notFoundComponent: RouteNotFound,
});

export const searchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  validateSearch: (s: Record<string, unknown>): SearchParams => {
    const mode = str(s.mode);
    return {
      q: str(s.q),
      mode: SEARCH_MODES.includes(mode as SearchMode) ? (mode as SearchMode) : undefined,
      collections: strList(s.collections),
      tags: strList(s.tags),
    };
  },
  component: SearchPage,
});

export const askRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ask",
  validateSearch: (s: Record<string, unknown>): { q?: string | undefined } => ({ q: str(s.q) }),
  component: lazyRouteComponent(() => import("@/features/ask/AskPage"), "AskPage"),
});

export const libraryRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/library",
  validateSearch: (s: Record<string, unknown>): LibraryParams => {
    const status = str(s.status);
    return {
      collection: str(s.collection),
      status: DOCUMENT_STATUSES.includes(status as DocumentStatus)
        ? (status as DocumentStatus)
        : undefined,
      tag: str(s.tag),
      q: str(s.q),
    };
  },
  component: lazyRouteComponent(() => import("@/features/library/LibraryPage"), "LibraryPage"),
});

export const readerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/documents/$id",
  component: lazyRouteComponent(() => import("@/features/reader/ReaderPage"), "ReaderPage"),
});

export const systemRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/system",
  component: lazyRouteComponent(() => import("@/features/system/SystemPage"), "SystemPage"),
});

const routeTree = rootRoute.addChildren([
  searchRoute,
  askRoute,
  libraryRoute,
  readerRoute,
  systemRoute,
]);

export function makeRouter(queryClient: QueryClient, history?: RouterHistory) {
  return createRouter({
    routeTree,
    ...(history ? { history } : {}),
    context: { queryClient },
    defaultPreload: "intent",
    defaultPreloadStaleTime: 0,
    defaultPendingComponent: RoutePending,
    defaultErrorComponent: RouteError,
    scrollRestoration: true,
  });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof makeRouter>;
  }
}
