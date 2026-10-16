import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import type { SearchRequest } from "@/lib/api/types";
import { searchResponse } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

describe("SearchPage", () => {
  it("shows the intro with recent queries when there is no query", async () => {
    renderApp("/");
    expect(await screen.findByRole("searchbox", { name: /search query/i })).toBeInTheDocument();
    expect(await screen.findByText("rank fusion")).toBeInTheDocument();
    expect(await screen.findByText("1,204")).toBeInTheDocument();
  });

  it("renders hits with breadcrumbs, safe highlighted snippets and timings", async () => {
    let body: SearchRequest | undefined;
    server.use(
      http.post("*/api/search", async ({ request }) => {
        body = (await request.json()) as SearchRequest;
        return HttpResponse.json(searchResponse);
      }),
    );
    const { container } = renderApp("/?q=fusion&mode=lexical&tags=rag");

    const results = await screen.findByRole("list", { name: /2 results/i });
    expect(body).toMatchObject({ query: "fusion", mode: "lexical", filters: { tags: ["rag"] } });

    const items = within(results).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(
      within(items[0] as HTMLElement).getByRole("heading", { name: "Hybrid retrieval notes" }),
    ).toBeInTheDocument();
    expect(within(items[0] as HTMLElement).getByText("Fusion")).toBeInTheDocument();
    expect(within(items[0] as HTMLElement).getByRole("link")).toHaveAttribute(
      "href",
      "/documents/8c7d2c55-0a52-4c38-9d0d-6a4a4c1f0a01#chunk-101",
    );

    // Only <mark> becomes markup; everything else is inert text.
    const marks = container.querySelectorAll("mark");
    expect([...marks].map((m) => m.textContent)).toEqual(["fusion", "fusion"]);
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
    expect(screen.getByText("&", { exact: false })).toBeInTheDocument();

    expect(screen.getByText("78 ms")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Lexical" })).toHaveAttribute("aria-checked", "true");
  });

  it("flags degraded stages and toggles the score inspector", async () => {
    server.use(
      http.post("*/api/search", () =>
        HttpResponse.json({ ...searchResponse, degraded: ["rerank"] }),
      ),
    );
    const user = userEvent.setup();
    renderApp("/?q=fusion");
    expect(await screen.findByText(/degraded · rerank/i)).toBeInTheDocument();
    expect(screen.queryByText("12.400")).toBeNull();
    await user.click(screen.getByRole("button", { name: /inspector/i }));
    expect(await screen.findByText("12.400")).toBeInTheDocument();
    expect(screen.getAllByText("Rerank")).toHaveLength(2);
  });

  it("moves through results with j/k and opens the active one with Enter", async () => {
    const user = userEvent.setup();
    const { router } = renderApp("/?q=fusion");
    await screen.findByRole("list", { name: /2 results/i });
    (document.activeElement as HTMLElement | null)?.blur();
    await user.keyboard("j");
    await user.keyboard("j");
    await waitFor(() =>
      expect(document.activeElement).toHaveAttribute("href", expect.stringContaining("#chunk-102")),
    );
    await user.keyboard("k");
    expect(document.activeElement).toHaveAttribute("href", expect.stringContaining("#chunk-101"));
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(router.state.location.pathname).toBe(
        "/documents/8c7d2c55-0a52-4c38-9d0d-6a4a4c1f0a01",
      ),
    );
  });

  it("shows an empty state and an error state", async () => {
    server.use(http.post("*/api/search", () => HttpResponse.json({ ...searchResponse, hits: [] })));
    renderApp("/?q=nothing");
    expect(await screen.findByText(/no passages found/i)).toBeInTheDocument();
  });

  it("surfaces API errors with a retry", async () => {
    server.use(
      http.post("*/api/search", () =>
        HttpResponse.json({ detail: "index unavailable" }, { status: 503 }),
      ),
    );
    renderApp("/?q=boom");
    expect(await screen.findByRole("alert")).toHaveTextContent("index unavailable");
    expect(screen.getByRole("button", { name: /^retry$/i })).toBeInTheDocument();
  });
});
