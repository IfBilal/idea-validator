import { useEffect, useReducer, useState } from "react";
import { clearSession, getToken } from "./auth";
import { initialRunState, isTerminal, runReducer } from "./runState";
import { streamRun, type StreamStatus } from "./sse";

/** Follow one run: folds its events into RunState and reconnects on its own. Stops at the end. */
export function useRun(runId: string | null) {
  const [state, dispatch] = useReducer(runReducer, initialRunState);
  const [status, setStatus] = useState<StreamStatus>("connecting");

  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    void streamRun({
      runId,
      token: getToken(),
      signal: controller.signal,
      onEvent: dispatch,
      onStatus: setStatus,
      onUnauthorized: clearSession,
    });
    return () => controller.abort();
  }, [runId]);

  return { state, status, finished: isTerminal(state.phase) };
}
