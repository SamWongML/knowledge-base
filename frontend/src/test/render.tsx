import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { makeRouter } from "@/router";

export function testQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Number.POSITIVE_INFINITY } },
  });
}

/** Render the full app at a URL (real route tree, memory history, MSW-backed API). */
export function renderApp(url: string) {
  const queryClient = testQueryClient();
  const router = makeRouter(queryClient, createMemoryHistory({ initialEntries: [url] }));
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...utils, router, queryClient };
}

/** Render an isolated component that needs router + query context. */
export function renderWithProviders(ui: ReactNode) {
  const queryClient = testQueryClient();
  const root = createRootRoute({ component: () => <>{ui}</> });
  const docs = createRoute({ getParentRoute: () => root, path: "/documents/$id" });
  const router = createRouter({
    routeTree: root.addChildren([docs]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}
