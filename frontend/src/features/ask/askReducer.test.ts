import { describe, expect, it } from "vitest";
import type { AgentStep, AskResponse } from "@/lib/api/types";
import { askReducer, initialAskState } from "./askReducer";

const step = (over: Partial<AgentStep>): AgentStep => ({
  type: "plan",
  message: "",
  query: null,
  hits: null,
  data: {},
  ...over,
});

describe("askReducer", () => {
  it("accumulates trace steps and answer deltas, then finalises on done", () => {
    let s = askReducer(initialAskState, { type: "start", question: "q?", now: 100 });
    s = askReducer(s, {
      type: "event",
      now: 150,
      event: { type: "step", step: step({ type: "plan" }) },
    });
    s = askReducer(s, {
      type: "event",
      now: 300,
      event: { type: "step", step: step({ type: "search", query: "x" }) },
    });
    s = askReducer(s, {
      type: "event",
      now: 400,
      event: { type: "step", step: step({ type: "answer_delta", message: "Hel" }) },
    });
    s = askReducer(s, {
      type: "event",
      now: 410,
      event: { type: "step", step: step({ type: "answer_delta", message: "lo [1]" }) },
    });
    expect(s.answer).toBe("Hello [1]");
    expect(s.steps.map((x) => [x.type, x.at])).toEqual([
      ["plan", 50],
      ["search", 200],
    ]);
    const response: AskResponse = {
      question: "q?",
      answer: "Hello [1].",
      citations: [
        { n: 1, chunk_id: 1, document_id: "d", document_title: "D", heading_path: "", snippet: "" },
      ],
      steps: [],
      engine: "local",
      total_ms: 320,
    };
    s = askReducer(s, { type: "event", now: 500, event: { type: "done", response } });
    expect(s.status).toBe("done");
    expect(s.answer).toBe("Hello [1].");
    expect(s.citations).toHaveLength(1);
  });

  it("records stream errors and cancellation", () => {
    let s = askReducer(initialAskState, { type: "start", question: "q", now: 0 });
    expect(askReducer(s, { type: "cancel" }).status).toBe("cancelled");
    s = askReducer(s, { type: "event", now: 1, event: { type: "error", message: "boom" } });
    expect(s).toMatchObject({ status: "error", error: "boom" });
  });
});
