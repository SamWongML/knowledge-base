import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef } from "react";
import { errorMessage } from "@/components/ui/States";
import { streamAsk } from "@/lib/api/endpoints";
import { keys } from "@/lib/api/queries";
import type { AskRequest } from "@/lib/api/types";
import { askReducer, initialAskState } from "./askReducer";

/** Run one streamed agent answer at a time; starting a new one aborts the previous. */
export function useAskStream() {
  const [state, dispatch] = useReducer(askReducer, initialAskState);
  const ctrl = useRef<AbortController | null>(null);
  const qc = useQueryClient();

  const stop = useCallback(() => {
    ctrl.current?.abort();
    ctrl.current = null;
    dispatch({ type: "cancel" });
  }, []);

  const ask = useCallback(
    async (req: AskRequest) => {
      ctrl.current?.abort();
      const c = new AbortController();
      ctrl.current = c;
      dispatch({ type: "start", question: req.question, now: performance.now() });
      let finished = false;
      try {
        for await (const event of streamAsk(req, c.signal)) {
          dispatch({ type: "event", event, now: performance.now() });
          if (event.type === "done" || event.type === "error") finished = true;
        }
        if (!finished && !c.signal.aborted) {
          dispatch({ type: "fail", message: "The stream ended before the answer was complete." });
        }
      } catch (err) {
        if (!c.signal.aborted) dispatch({ type: "fail", message: errorMessage(err) });
      } finally {
        if (ctrl.current === c) ctrl.current = null;
        void qc.invalidateQueries({ queryKey: keys.recent });
      }
    },
    [qc],
  );

  useEffect(() => () => ctrl.current?.abort(), []);

  return { state, ask, stop, reset: () => dispatch({ type: "reset" }) };
}
