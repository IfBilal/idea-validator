import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { clearSession, resetAuthForTests, setSession } from "@/lib/auth";
import { parseTrace } from "@/lib/trace";
import { makeRouter } from "@/router";
import fixture from "./fixtures/trace_tree.json";

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

function renderAt(path = "/runs/run_abc/trace") {
  const router = makeRouter(createMemoryHistory({ initialEntries: [path] }));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const envelope = (code: string, message: string) => ({ error: { code, message } });

describe("parseTrace", () => {
  it("accepts the real response shape", () => {
    expect(parseTrace(fixture)).not.toBeNull();
  });
  it("fails closed on a wrong shape", () => {
    expect(parseTrace({ ...fixture, totals: undefined })).toBeNull();
    expect(parseTrace({ ...fixture, tree: [{ record_id: "r1" }] })).toBeNull();
    expect(parseTrace({ ...fixture, tree: "nope" })).toBeNull();
    expect(parseTrace(null)).toBeNull();
    const bad = structuredClone(fixture);
    bad.tree[0]!.record.kind = "mystery";
    expect(parseTrace(bad)).toBeNull();
  });
});

describe("trace page", () => {
  it("shows totals exactly as returned, per run and per agent", async () => {
    server.use(http.get(`${API}/runs/run_abc/trace`, () => HttpResponse.json(fixture)));
    renderAt();
    const run = await screen.findByRole("region", { name: /run totals/i });
    expect(within(run).getByText("4")).toBeInTheDocument(); // steps
    expect(within(run).getByText("2000")).toBeInTheDocument(); // tokens in
    expect(within(run).getByText("400")).toBeInTheDocument(); // tokens out
    expect(within(run).getByText("$0.0163")).toBeInTheDocument();
    expect(within(run).getByText("12600 ms")).toBeInTheDocument();
    const agents = screen.getByRole("region", { name: /totals by agent/i });
    const researcher = within(agents).getByRole("row", { name: /researcher/ });
    expect(within(researcher).getByText("$0.0123")).toBeInTheDocument();
    expect(within(researcher).getByText("1200")).toBeInTheDocument();
    const risk = within(agents).getByRole("row", { name: /risk/ });
    expect(within(risk).getAllByText("1")).toHaveLength(2); // steps and errors
  });

  it("renders the tree with kind, name, duration, tokens, cost and status", async () => {
    server.use(http.get(`${API}/runs/run_abc/trace`, () => HttpResponse.json(fixture)));
    renderAt();
    const tree = await screen.findByRole("tree", { name: /trace steps/i });
    const items = within(tree).getAllByRole("treeitem");
    expect(items).toHaveLength(4);
    const root = items[0]!;
    expect(root).toHaveAttribute("aria-level", "1");
    expect(root).toHaveAttribute("aria-expanded", "true");
    expect(within(root).getAllByText("decision").length).toBeGreaterThan(0);
    const search = screen.getByRole("treeitem", { name: /web_search/ });
    expect(search).toHaveAttribute("aria-level", "2");
    expect(within(search).getByText("tool_call")).toBeInTheDocument();
    expect(within(search).getByText("1200 ms")).toBeInTheDocument();
    const llm = screen.getByRole("treeitem", { name: /gpt-x/ });
    expect(within(llm).getByText("llm_call")).toBeInTheDocument();
    expect(within(llm).getByText(/1200 in/)).toBeInTheDocument();
    expect(within(llm).getByText(/300 out/)).toBeInTheDocument();
    expect(within(llm).getByText("$0.0123")).toBeInTheDocument();
    expect(within(llm).getByText("ok")).toBeInTheDocument();
    const failed = screen.getByRole("treeitem", { name: /risk/ });
    expect(within(failed).getByText("error: timeout")).toBeInTheDocument();
  });

  it("collapses and expands with the keyboard", async () => {
    server.use(http.get(`${API}/runs/run_abc/trace`, () => HttpResponse.json(fixture)));
    renderAt();
    const toggle = await screen.findByRole("button", { name: /collapse .*orchestrator/i });
    toggle.focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getAllByRole("treeitem")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /expand .*orchestrator/i })).toHaveAttribute("aria-expanded", "false");
    await userEvent.keyboard(" ");
    expect(screen.getAllByRole("treeitem")).toHaveLength(4);
  });

  it("says so when the run has no steps", async () => {
    const empty = { ...fixture, tree: [], by_agent: {}, totals: { ...fixture.totals, steps: 0 } };
    server.use(http.get(`${API}/runs/run_abc/trace`, () => HttpResponse.json(empty)));
    renderAt();
    expect(await screen.findByText(/no steps were recorded/i)).toBeInTheDocument();
  });

  it("notes when the trace was truncated", async () => {
    server.use(http.get(`${API}/runs/run_abc/trace`, () => HttpResponse.json({ ...fixture, truncated: true })));
    renderAt();
    expect(await screen.findByText(/trace was cut short/i)).toBeInTheDocument();
  });

  it("gives one message for unknown and foreign runs (404)", async () => {
    server.use(http.get(`${API}/runs/run_abc/trace`, () => HttpResponse.json(envelope("NOT_FOUND", "run not found"), { status: 404 })));
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent(/no trace found for this run/i);
  });

  it("explains 503 as traces being unavailable", async () => {
    server.use(http.get(`${API}/runs/run_abc/trace`, () => HttpResponse.json(envelope("UNAVAILABLE", "run traces are not available"), { status: 503 })));
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent(/traces are unavailable right now/i);
  });

  it("fails closed on a malformed response", async () => {
    server.use(http.get(`${API}/runs/run_abc/trace`, () => HttpResponse.json({ run_id: "run_abc", tree: "x" })));
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent(/does not understand/i);
    expect(screen.queryByRole("tree")).toBeNull();
  });

  it("refuses a trace that belongs to another run id", async () => {
    server.use(http.get(`${API}/runs/run_zzz/trace`, () => HttpResponse.json(fixture)));
    renderAt("/runs/run_zzz/trace");
    expect(await screen.findByRole("alert")).toHaveTextContent(/does not understand/i);
  });

  it("sends anonymous visitors to the login page", async () => {
    clearSession();
    const router = renderAt();
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
  });
});

describe("view trace link", () => {
  it("is offered on the validate result for the user's own run", async () => {
    const card = (await import("./fixtures/m11_go.json")).default;
    const frame = (seq: number, type: string, payload: object = {}) =>
      `event: ${type}\nid: ${seq}\ndata: ${JSON.stringify({ run_id: "run1", seq, ts: "2026-01-01T00:00:00Z", type, payload })}\n\n`;
    server.use(
      http.post(`${API}/validate`, () => HttpResponse.json({ run_id: "run1", idea_id: "idea1", version: 1 }, { status: 202 })),
      http.get(
        `${API}/runs/run1/events`,
        () =>
          new HttpResponse([frame(1, "run_started", {}), frame(2, "verdict", card), frame(3, "run_completed", {})].join(""), {
            headers: { "Content-Type": "text/event-stream" },
          }),
      ),
    );
    renderAt("/validate");
    await userEvent.type(await screen.findByLabelText("Title"), "Notes exporter");
    await userEvent.type(screen.getByLabelText("Your idea"), "A CLI that exports Notion pages to clean Markdown.");
    await userEvent.click(screen.getByRole("button", { name: /^validate$/i }));
    const link = await screen.findByRole("link", { name: /view trace/i });
    expect(link).toHaveAttribute("href", "/runs/run1/trace");
  });
});
