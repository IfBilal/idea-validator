import { z } from "zod";
import { loadConfig } from "./config";

/** One parsed Server-Sent-Events frame. */
export interface SseFrame {
  event: string;
  id?: string;
  data: string;
}

/**
 * Incremental SSE parser (WHATWG rules): handles \n, \r\n and \r line ends, frames split across
 * chunks, multi-line data, comment lines (":") and ignores unknown fields.
 */
export function createSseParser(onFrame: (frame: SseFrame) => void) {
  let buffer = "";
  let event = "";
  let id: string | undefined;
  let data: string[] = [];
  let skipLeadingLf = false;

  function dispatch() {
    if (data.length > 0) onFrame({ event: event || "message", id, data: data.join("\n") });
    event = "";
    data = [];
    // `id` persists across frames per the spec only for Last-Event-ID; we report per-frame ids
    id = undefined;
  }

  function line(text: string) {
    if (text === "") return dispatch();
    if (text.startsWith(":")) return;
    const colon = text.indexOf(":");
    const field = colon === -1 ? text : text.slice(0, colon);
    let value = colon === -1 ? "" : text.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
    else if (field === "id" && !value.includes("\0")) id = value;
  }

  return {
    push(chunk: string) {
      let text = chunk;
      if (skipLeadingLf && text.startsWith("\n")) text = text.slice(1);
      skipLeadingLf = false;
      buffer += text;
      let start = 0;
      for (let i = 0; i < buffer.length; i++) {
        const ch = buffer[i];
        if (ch === "\n" || ch === "\r") {
          line(buffer.slice(start, i));
          if (ch === "\r") {
            if (buffer[i + 1] === "\n") i++;
            else if (i + 1 === buffer.length) skipLeadingLf = true;
          }
          start = i + 1;
        }
      }
      buffer = buffer.slice(start);
    },
  };
}

export const RUN_EVENT_TYPES = [
  "run_started",
  "state_changed",
  "agent_started",
  "agent_finished",
  "tool_call",
  "llm_call",
  "spend_update",
  "question",
  "approval_required",
  "idea_dropped",
  "partial_result",
  "verdict",
  "run_completed",
  "run_failed",
  "budget_stopped",
  "heartbeat",
] as const;

export const RunEvent = z.object({
  run_id: z.string(),
  seq: z.number().int(),
  ts: z.string(),
  type: z.enum(RUN_EVENT_TYPES),
  payload: z.record(z.string(), z.unknown()),
});
export type RunEvent = z.infer<typeof RunEvent>;

export const TERMINAL_EVENTS = new Set<RunEvent["type"]>(["run_completed", "run_failed", "budget_stopped"]);

export type StreamStatus = "connecting" | "live" | "reconnecting" | "closed";

export interface StreamOptions {
  runId: string;
  token: string | null;
  signal: AbortSignal;
  onEvent: (event: RunEvent) => void;
  onStatus?: (status: StreamStatus) => void;
  onUnauthorized?: () => void;
  /** test seam */
  sleep?: (ms: number) => Promise<void>;
  maxAttempts?: number;
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Follow a run's event stream with fetch (EventSource cannot send Authorization). Reconnects with
 * `Last-Event-ID` and exponential back-off (1 s .. 15 s); resets the back-off whenever events arrive;
 * stops after a terminal event, an abort, a 401/404, or `maxAttempts` consecutive failures.
 */
export async function streamRun(options: StreamOptions): Promise<void> {
  const { runId, token, signal, onEvent, onStatus, onUnauthorized } = options;
  const sleep = options.sleep ?? defaultSleep;
  const maxAttempts = options.maxAttempts ?? 8;
  let lastId: string | undefined;
  let failures = 0;
  let done = false;

  while (!done && !signal.aborted) {
    onStatus?.(lastId === undefined && failures === 0 ? "connecting" : "reconnecting");
    let waitMs = Math.min(15_000, 1000 * 2 ** failures);
    try {
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      if (token) headers.Authorization = `Bearer ${token}`;
      if (lastId !== undefined) headers["Last-Event-ID"] = lastId;
      const response = await fetch(`${loadConfig().API_BASE_URL}/runs/${encodeURIComponent(runId)}/events`, {
        headers,
        signal,
      });
      if (response.status === 401) {
        onUnauthorized?.();
        break;
      }
      if (response.status === 404) break; // unknown or foreign run: nothing to follow
      if (response.status === 429 || response.status === 503) {
        const retry = Number.parseInt(response.headers.get("Retry-After") ?? "", 10);
        waitMs = Number.isFinite(retry) && retry >= 0 ? retry * 1000 : waitMs;
        throw new Error(`HTTP ${response.status}`);
      }
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

      onStatus?.("live");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const parser = createSseParser((frame) => {
        if (frame.event === "heartbeat") return;
        let parsed: RunEvent;
        try {
          parsed = RunEvent.parse(JSON.parse(frame.data));
        } catch {
          return; // a frame this app does not understand is skipped, never trusted
        }
        if (frame.id !== undefined) lastId = frame.id;
        failures = 0;
        onEvent(parsed);
        if (TERMINAL_EVENTS.has(parsed.type)) done = true;
      });
      for (;;) {
        const { value, done: finished } = await reader.read();
        if (finished) break;
        parser.push(decoder.decode(value, { stream: true }));
        if (done) {
          await reader.cancel();
          break;
        }
      }
      if (!done) failures += 1; // the server closed the stream (e.g. SSE_MAX_S): resume from lastId
    } catch {
      if (signal.aborted) break;
      failures += 1;
    }
    if (!done && !signal.aborted) {
      if (failures >= maxAttempts) break;
      await sleep(waitMs);
    }
  }
  onStatus?.("closed");
}
