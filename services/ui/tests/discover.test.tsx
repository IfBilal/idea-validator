import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { clearSession, resetAuthForTests, setSession } from "@/lib/auth";
import { parseDiscoverResult } from "@/lib/discover";
import { makeRouter } from "@/router";

const API = "http://gw.test";
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  window.__APP_CONFIG__ = { API_BASE_URL: API };
  window.sessionStorage.clear();
  resetAuthForTests();
  setSession("tok", 3600);
});
afterEach(() => {
  server.resetHandlers();
  clearSession();
});

const frame = (seq: number, type: string, payload: object = {}) =>
  `event: ${type}\nid: ${seq}\ndata: ${JSON.stringify({ run_id: "run1", seq, ts: "2026-01-01T00:00:00Z", type, payload })}\n\n`;
const sse = (...frames: string[]) => new HttpResponse(frames.join(""), { headers: { "Content-Type": "text/event-stream" } });

function renderAt(path = "/discover") {
  const router = makeRouter(createMemoryHistory({ initialEntries: [path] }));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const idea = (over: object = {}) => ({
  idea_id: "idea_a",
  version: 1,
  title: "Notion exporter",
  summary: "Export pages to Markdown.",
  niche: "devtools",
  verdict: "GO",
  stability: "STABLE",
  score: 72.5,
  confidence: 0.81,
  reason: "Strong demand, thin competition.",
  gap_evidence_ids: ["card_1"],
  ...over,
});

const accept = () => http.post(`${API}/discover`, () => HttpResponse.json({ run_id: "run1" }, { status: 202 }));
const finish = (...extra: string[]) =>
  http.get(`${API}/runs/run1/events`, () => sse(frame(1, "run_started", { kind: "discover" }), ...extra));
const result = (body: unknown) => http.get(`${API}/runs/run1`, () => HttpResponse.json({ state: "succeeded", result: body }));

async function run(niche?: string) {
  if (niche) await userEvent.type(await screen.findByLabelText(/niche/i), niche);
  await userEvent.click(await screen.findByRole("button", { name: /^discover$/i }));
}

describe("discover page", () => {
  it("redirects anonymous visitors to login", async () => {
    clearSession();
    const router = renderAt();
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
  });

  it("posts niche and count (default 5) and shows the ranked ideas exactly as returned", async () => {
    let body: unknown;
    server.use(
      http.post(`${API}/discover`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ run_id: "run1" }, { status: 202 });
      }),
      finish(frame(2, "run_completed", {})),
      result({
        ideas: [
          idea(),
          idea({ idea_id: "idea_b", title: "Second", verdict: "PIVOT", stability: "BORDERLINE", score: 55.25, confidence: 0.6 }),
        ],
        dropped: [],
      }),
    );
    renderAt();
    await run("devtools");
    expect(body).toEqual({ niche: "devtools", count: 5, deep_search: false });
    const list = await screen.findByRole("list", { name: "Ranked ideas" });
    const items = within(list).getAllByRole("listitem") as [HTMLElement, HTMLElement];
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText("Notion exporter")).toBeInTheDocument();
    expect(within(items[0]).getByText("GO")).toBeInTheDocument();
    expect(within(items[0]).getByText("STABLE")).toBeInTheDocument();
    expect(within(items[0]).getByText("72.5")).toBeInTheDocument();
    expect(within(items[0]).getByText("0.81")).toBeInTheDocument();
    expect(within(items[0]).getByText("Export pages to Markdown.")).toBeInTheDocument();
    expect(within(items[1]).getByText("55.25")).toBeInTheDocument();
    expect(within(items[1]).getByText("PIVOT")).toBeInTheDocument();
  });

  it("sends niche null when left empty and honours a custom count", async () => {
    let body: unknown;
    server.use(
      http.post(`${API}/discover`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ run_id: "run1" }, { status: 202 });
      }),
      finish(frame(2, "run_completed", {})),
      result({ ideas: [], dropped: [] }),
    );
    renderAt();
    const count = await screen.findByLabelText(/how many/i);
    await userEvent.clear(count);
    await userEvent.type(count, "10");
    await run();
    await screen.findByRole("heading", { name: /^done$/i });
    expect(body).toEqual({ niche: null, count: 10, deep_search: false });
  });

  it.each(["0", "11", "2.5", ""])("refuses count %j before calling the gateway", async (value) => {
    let called = false;
    server.use(
      http.post(`${API}/discover`, () => {
        called = true;
        return HttpResponse.json({ run_id: "x" }, { status: 202 });
      }),
    );
    renderAt();
    const count = await screen.findByLabelText(/how many/i);
    await userEvent.clear(count);
    if (value) await userEvent.type(count, value);
    await userEvent.click(screen.getByRole("button", { name: /^discover$/i }));
    expect(await screen.findByText(/whole number from 1 to 10/i)).toBeInTheDocument();
    expect(called).toBe(false);
  });

  it.each([1, 10])("accepts the boundary count %i", async (n) => {
    let count = 0;
    server.use(
      http.post(`${API}/discover`, async ({ request }) => {
        count = ((await request.json()) as { count: number }).count;
        return HttpResponse.json({ run_id: "run1" }, { status: 202 });
      }),
      finish(),
    );
    renderAt();
    const field = await screen.findByLabelText(/how many/i);
    await userEvent.clear(field);
    await userEvent.type(field, String(n));
    await run();
    await waitFor(() => expect(count).toBe(n));
  });

  it("limits the niche to 200 characters", async () => {
    renderAt();
    expect(await screen.findByLabelText(/niche/i)).toHaveAttribute("maxlength", "200");
  });

  it("shows an empty result in plain words", async () => {
    server.use(accept(), finish(frame(2, "run_completed", {})), result({ ideas: [], dropped: [] }));
    renderAt();
    await run();
    expect(await screen.findByText(/no ideas survived/i)).toBeInTheDocument();
  });

  it("shows an unvalidated idea as not validated, with no verdict, score or stability", async () => {
    server.use(
      accept(),
      finish(frame(2, "run_completed", {})),
      result({
        ideas: [
          idea({ idea_id: "p", title: "Half done", verdict: null, stability: null, score: null, confidence: null, partial: true, needs_input: ["estimated_effort_hours"] }),
        ],
        dropped: [],
        partial: true,
      }),
    );
    renderAt();
    await run();
    const list = await screen.findByRole("list", { name: "Ranked ideas" });
    const item = within(list).getByRole("listitem");
    expect(within(item).getByText(/not validated/i)).toBeInTheDocument();
    expect(within(item).queryByText(/^(GO|PIVOT|NO-GO)$/)).not.toBeInTheDocument();
    expect(within(item).queryByText(/score/i)).not.toBeInTheDocument();
    expect(screen.getByText(/some ideas are not fully validated/i)).toBeInTheDocument();
  });

  it("lists dropped ideas with reasons, from the result", async () => {
    server.use(
      accept(),
      finish(frame(2, "idea_dropped", { title: "Todo app", round: 1 }), frame(3, "run_completed", {})),
      result({
        ideas: [idea()],
        dropped: [
          { title: "Todo app", round: 1, reason: "fatal_objection", defeated_by_evidence_ids: ["c1", "c2"] },
          { title: "Chat app", round: 2, reason: "no_gap_evidence", defeated_by_evidence_ids: [] },
        ],
      }),
    );
    renderAt();
    await run();
    const dropped = await screen.findByRole("list", { name: "Dropped ideas" });
    expect(within(dropped).getByText("Todo app")).toBeInTheDocument();
    expect(within(dropped).getByText(/a fatal objection was upheld/i)).toBeInTheDocument();
    expect(within(dropped).getByText(/no evidence of a market gap/i)).toBeInTheDocument();
  });

  it("shows dropped ideas streamed during the run before the result arrives", async () => {
    server.use(accept(), finish(frame(2, "idea_dropped", { title: "Streamed drop", round: 1 })));
    renderAt();
    await run();
    expect(await screen.findByText("Streamed drop")).toBeInTheDocument();
  });

  it("explains a budget stop and still shows the partial results", async () => {
    server.use(
      accept(),
      finish(frame(2, "budget_stopped", { reason: "token budget" })),
      http.get(`${API}/runs/run1`, () =>
        HttpResponse.json({ state: "budget_stopped", result: { ideas: [idea()], dropped: [], partial: true } }),
      ),
    );
    renderAt();
    await run();
    expect(await screen.findByText(/stopped because it reached its budget/i)).toBeInTheDocument();
    expect(await screen.findByText("Notion exporter")).toBeInTheDocument();
    expect(screen.getByText(/results are partial/i)).toBeInTheDocument();
  });

  it("a failed run shows the failure and no ideas", async () => {
    server.use(
      accept(),
      finish(frame(2, "run_failed", { reason: "scout unavailable" })),
      http.get(`${API}/runs/run1`, () => HttpResponse.json({ state: "failed", result: null })),
    );
    renderAt();
    await run();
    expect(await screen.findByText(/could not finish/i)).toBeInTheDocument();
    expect(await screen.findByText(/no result was produced/i)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Ranked ideas" })).not.toBeInTheDocument();
  });

  it("a malformed result fails closed: error, no ideas, no verdicts", async () => {
    server.use(
      accept(),
      finish(frame(2, "run_completed", {})),
      result({ ideas: [idea({ verdict: "GO", stability: null, score: null })], dropped: [] }),
    );
    renderAt();
    await run();
    expect(await screen.findByText(/could not be displayed/i)).toBeInTheDocument();
    expect(screen.queryByText("GO")).not.toBeInTheDocument();
  });

  it("opens the idea card from a result", async () => {
    server.use(
      accept(),
      finish(frame(2, "run_completed", {})),
      result({ ideas: [idea()], dropped: [] }),
      http.get(`${API}/ideas/idea_a/versions/1`, () =>
        HttpResponse.json({ verdict: null, partial: true, needs_input: ["estimated_effort_hours"] }),
      ),
    );
    const router = renderAt();
    await run();
    await userEvent.click(await screen.findByRole("link", { name: /open card.*notion exporter/i }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/ideas/idea_a"));
    expect(router.state.location.search).toMatchObject({ v: 1 });
    expect(await screen.findByRole("heading", { name: /needs your input/i })).toBeInTheDocument();
  });

  it.each([
    [429, "RATE_LIMITED", /too many attempts/i],
    [503, "UNAVAILABLE", /something went wrong on our side/i],
  ])("shows a plain error for HTTP %i and keeps the form", async (status, code, text) => {
    server.use(http.post(`${API}/discover`, () => HttpResponse.json({ error: { code, message: "x" } }, { status })));
    renderAt();
    await run("devtools");
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
    expect(screen.getByLabelText(/niche/i)).toHaveValue("devtools");
  });

  it("has a Discover link in the navigation", async () => {
    renderAt("/profile");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Discover" })).toBeInTheDocument();
  });
});

describe("parseDiscoverResult", () => {
  it("rejects garbage and verdict-less contradictions", () => {
    expect(parseDiscoverResult(null)).toBeNull();
    expect(parseDiscoverResult({ ideas: "x" })).toBeNull();
    expect(parseDiscoverResult({ ideas: [idea({ verdict: null, score: 50 })], dropped: [] })).toBeNull();
    expect(parseDiscoverResult({ ideas: [idea({ verdict: "MAYBE" })], dropped: [] })).toBeNull();
    expect(parseDiscoverResult({ ideas: [idea({ partial: true })], dropped: [] })).toBeNull();
  });
  it("accepts a verdict-less idea and keeps order", () => {
    const r = parseDiscoverResult({ ideas: [idea({ idea_id: "b" }), idea({ idea_id: "a" })], dropped: [] });
    expect(r?.ideas.map((i) => i.idea_id)).toEqual(["b", "a"]);
  });
});
