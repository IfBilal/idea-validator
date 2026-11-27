import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { createSseParser, streamRun, type RunEvent, type SseFrame, type StreamStatus } from "@/lib/sse";

function parse(chunks: string[]): SseFrame[] {
  const frames: SseFrame[] = [];
  const parser = createSseParser((f) => frames.push(f));
  chunks.forEach((c) => parser.push(c));
  return frames;
}

describe("SSE parser", () => {
  it("parses a frame with event, id and data", () => {
    expect(parse(["event: spend_update\nid: 7\ndata: {\"a\":1}\n\n"])).toEqual([
      { event: "spend_update", id: "7", data: '{"a":1}' },
    ]);
  });

  it("handles CRLF, lone CR and frames split across chunks at any byte", () => {
    const text = "event: a\r\nid: 1\r\ndata: x\r\n\r\nevent: b\rdata: y\r\r";
    for (let cut = 1; cut < text.length; cut++) {
      expect(parse([text.slice(0, cut), text.slice(cut)])).toEqual([
        { event: "a", id: "1", data: "x" },
        { event: "b", id: undefined, data: "y" },
      ]);
    }
  });

  it("joins multi-line data and skips comments and unknown fields", () => {
    expect(parse([": keep-alive\nretry: 5\nevent: m\ndata: a\ndata: b\n\n"])).toEqual([
      { event: "m", id: undefined, data: "a\nb" },
    ]);
  });

  it("defaults the event name and drops frames with no data", () => {
    expect(parse(["data: hi\n\n", "event: only\n\n"])).toEqual([{ event: "message", id: undefined, data: "hi" }]);
  });

  it("does not dispatch an incomplete trailing frame", () => {
    expect(parse(["data: one\n\ndata: two"])).toHaveLength(1);
  });
});

const API = "http://gw.test";
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  window.__APP_CONFIG__ = { API_BASE_URL: API };
});
afterEach(() => server.resetHandlers());

const frame = (seq: number, type: string, payload: object = {}) =>
  `event: ${type}\nid: ${seq}\ndata: ${JSON.stringify({ run_id: "r1", seq, ts: "2026-01-01T00:00:00Z", type, payload })}\n\n`;

function sseResponse(body: string, status = 200, headers: Record<string, string> = {}) {
  return new HttpResponse(body, { status, headers: { "Content-Type": "text/event-stream", ...headers } });
}

async function run(extra: Partial<Parameters<typeof streamRun>[0]> = {}) {
  const events: RunEvent[] = [];
  const statuses: StreamStatus[] = [];
  const unauthorized: string[] = [];
  await streamRun({
    runId: "r1",
    token: "tok",
    signal: new AbortController().signal,
    onEvent: (e) => events.push(e),
    onStatus: (s) => statuses.push(s),
    onUnauthorized: () => unauthorized.push("x"),
    sleep: async () => {},
    ...extra,
  });
  return { events, statuses, unauthorized };
}

describe("streamRun", () => {
  it("sends the bearer token, delivers validated events and stops at the terminal event", async () => {
    let auth: string | null = null;
    server.use(
      http.get(`${API}/runs/r1/events`, ({ request }) => {
        auth = request.headers.get("Authorization");
        return sseResponse(frame(1, "run_started") + frame(2, "run_completed", { status: "ok" }));
      }),
    );
    const { events, statuses } = await run();
    expect(auth).toBe("Bearer tok");
    expect(events.map((e) => e.type)).toEqual(["run_started", "run_completed"]);
    expect(statuses).toContain("live");
    expect(statuses.at(-1)).toBe("closed");
  });

  it("ignores heartbeats and frames that fail validation", async () => {
    server.use(
      http.get(`${API}/runs/r1/events`, () =>
        sseResponse(
          "event: heartbeat\ndata: {}\n\n" +
            "event: nonsense\nid: 5\ndata: not json\n\n" +
            'event: tool_call\nid: 6\ndata: {"run_id":"r1","seq":"six","ts":"t","type":"tool_call","payload":{}}\n\n' +
            frame(7, "run_completed"),
        ),
      ),
    );
    const { events } = await run();
    expect(events.map((e) => e.seq)).toEqual([7]);
  });

  it("reconnects with Last-Event-ID after the server closes the stream", async () => {
    const seen: (string | null)[] = [];
    server.use(
      http.get(`${API}/runs/r1/events`, ({ request }) => {
        seen.push(request.headers.get("Last-Event-ID"));
        return seen.length === 1
          ? sseResponse(frame(1, "run_started") + frame(2, "agent_started", { agent: "a" }))
          : sseResponse(frame(3, "run_completed"));
      }),
    );
    const { events } = await run();
    expect(seen).toEqual([null, "2"]);
    expect(events.map((e) => e.seq)).toEqual([1, 2, 3]);
  });

  it("backs off and retries after a failure, then gives up after maxAttempts", async () => {
    let calls = 0;
    const waits: number[] = [];
    server.use(
      http.get(`${API}/runs/r1/events`, () => {
        calls += 1;
        return new HttpResponse("boom", { status: 500 });
      }),
    );
    await run({ maxAttempts: 3, sleep: async (ms) => void waits.push(ms) });
    expect(calls).toBe(3);
    expect(waits).toEqual([1000, 2000]);
  });

  it("honours Retry-After on 429", async () => {
    const waits: number[] = [];
    let calls = 0;
    server.use(
      http.get(`${API}/runs/r1/events`, () => {
        calls += 1;
        return calls === 1
          ? new HttpResponse("slow", { status: 429, headers: { "Retry-After": "5" } })
          : sseResponse(frame(1, "run_completed"));
      }),
    );
    const { events } = await run({ sleep: async (ms) => void waits.push(ms) });
    expect(waits).toEqual([5000]);
    expect(events).toHaveLength(1);
  });

  it("reports an expired session and stops", async () => {
    let calls = 0;
    server.use(
      http.get(`${API}/runs/r1/events`, () => {
        calls += 1;
        return new HttpResponse("no", { status: 401 });
      }),
    );
    const { unauthorized } = await run();
    expect(unauthorized).toHaveLength(1);
    expect(calls).toBe(1);
  });

  it("stops quietly on 404 (unknown or someone else's run)", async () => {
    let calls = 0;
    server.use(
      http.get(`${API}/runs/r1/events`, () => {
        calls += 1;
        return new HttpResponse("{}", { status: 404 });
      }),
    );
    const { events } = await run();
    expect(calls).toBe(1);
    expect(events).toEqual([]);
  });

  it("stops when aborted", async () => {
    const controller = new AbortController();
    server.use(http.get(`${API}/runs/r1/events`, () => sseResponse(frame(1, "run_started"))));
    controller.abort();
    const { events, statuses } = await run({ signal: controller.signal });
    expect(events).toEqual([]);
    expect(statuses.at(-1)).toBe("closed");
  });
});
