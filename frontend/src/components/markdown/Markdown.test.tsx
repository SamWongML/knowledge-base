import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Citation } from "@/lib/api/types";
import { renderWithProviders } from "@/test/render";
import { Markdown } from "./Markdown";

const citation: Citation = {
  n: 1,
  chunk_id: 42,
  document_id: "doc-1",
  document_title: "Fusion notes",
  heading_path: "Retrieval > RRF",
  snippet: "rank <mark>fusion</mark>",
};

describe("Markdown citations", () => {
  it("turns [n] markers into links to the cited chunk, but not inside code", async () => {
    renderWithProviders(
      <Markdown citations={[citation]}>
        {"RRF works well [1]. Literal `[1]` stays.\n\n[2] is unknown."}
      </Markdown>,
    );
    const link = await screen.findByRole("link", { name: /source 1: fusion notes/i });
    expect(link).toHaveAttribute("href", "/documents/doc-1#chunk-42");
    expect(screen.getByText("[1]", { selector: "code" })).toBeInTheDocument();
    // Unknown citation renders as an inert marker.
    await waitFor(() => expect(screen.getByTitle("Source 2")).toHaveTextContent("2"));
  });

  it("renders plain markdown without citation handling when none are given", async () => {
    renderWithProviders(<Markdown>{"# Title\n\nsee [1]"}</Markdown>);
    expect(await screen.findByRole("heading", { name: "Title" })).toBeInTheDocument();
    expect(screen.getByText(/see \[1\]/)).toBeInTheDocument();
  });
});
