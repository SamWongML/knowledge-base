import type { AskStreamEvent } from "@/lib/api/endpoints";
import type { AgentStep, AskResponse, Citation } from "@/lib/api/types";

export interface TraceStep extends AgentStep {
  /** Stable key and the time it arrived (ms since the run started). */
  key: number;
  at: number;
}

export interface AskState {
  status: "idle" | "streaming" | "done" | "error" | "cancelled";
  question: string;
  steps: TraceStep[];
  answer: string;
  citations: Citation[];
  response: AskResponse | null;
  error: string | null;
  startedAt: number;
}

export const initialAskState: AskState = {
  status: "idle",
  question: "",
  steps: [],
  answer: "",
  citations: [],
  response: null,
  error: null,
  startedAt: 0,
};

export type AskAction =
  | { type: "start"; question: string; now: number }
  | { type: "event"; event: AskStreamEvent; now: number }
  | { type: "fail"; message: string }
  | { type: "cancel" }
  | { type: "reset" };

export function askReducer(state: AskState, action: AskAction): AskState {
  switch (action.type) {
    case "start":
      return {
        ...initialAskState,
        status: "streaming",
        question: action.question,
        startedAt: action.now,
      };
    case "fail":
      return { ...state, status: "error", error: action.message };
    case "cancel":
      return state.status === "streaming" ? { ...state, status: "cancelled" } : state;
    case "reset":
      return initialAskState;
    case "event": {
      const ev = action.event;
      if (ev.type === "done") {
        return {
          ...state,
          status: "done",
          answer: ev.response.answer || state.answer,
          citations: ev.response.citations,
          response: ev.response,
        };
      }
      if (ev.type === "error") return { ...state, status: "error", error: ev.message };
      const step = ev.step;
      if (step.type === "answer_delta") return { ...state, answer: state.answer + step.message };
      if (step.type === "answer") {
        return {
          ...state,
          answer: step.message || state.answer,
          steps: [
            ...state.steps,
            { ...step, key: state.steps.length, at: action.now - state.startedAt },
          ],
        };
      }
      return {
        ...state,
        steps: [
          ...state.steps,
          { ...step, key: state.steps.length, at: action.now - state.startedAt },
        ],
      };
    }
  }
}
