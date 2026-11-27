import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { clearSession, resetAuthForTests, setSession } from "@/lib/auth";
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

const sse = (...frames: string[]) =>
  new HttpResponse(frames.join(""), { headers: { "Content-Type": "text/event-stream" } });

function renderValidate() {
  const router = makeRouter(createMemoryHistory({ initialEntries: ["/validate"] }));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

async function submitIdea() {
  await userEvent.type(await screen.findByLabelText("Title"), "Notes exporter");
  await userEvent.type(screen.getByLabelText("Your idea"), "A CLI that exports Notion pages to clean Markdown.");
  await userEvent.click(screen.getByRole("button", { name: /^validate$/i }));
}

describe("validate page", () => {
  it("validates the form before calling the gateway", async () => {
    renderValidate();
    await userEvent.click(await screen.findByRole("button", { name: /^validate$/i }));
    expect(await screen.findByText("Give your idea a short title")).toBeInTheDocument();
    expect(screen.getByText("Describe the idea in at least a sentence")).toBeInTheDocument();
  });

  it("starts a run with an idempotency key and shows live progress, spend and tool calls", async () => {
    let key: string | null = null;
    let body: unknown;
    server.use(
      http.post(`${API}/validate`, async ({ request }) => {
        key = request.headers.get("Idempotency-Key");
        body = await request.json();
        return HttpResponse.json({ run_id: "run1", idea_id: "idea1", version: 1 }, { status: 202 });
      }),
      http.get(`${API}/runs/run1/events`, () =>
        sse(
          frame(1, "run_started", { kind: "validate", budget: { cost_usd: 0.05 } }),
          frame(2, "agent_started", { agent: "Competition scorer", step_id: "s1" }),
          frame(3, "tool_call", { agent: "x", tool: "kb.search", input_summary: "notion export", latency_ms: 42, cache_hit: true }),
          frame(4, "spend_update", { spent: { cost_usd: 0.042, tokens: 1234, pages: 3 }, budget: { cost_usd: 0.05 } }),
        ),
      ),
    );
    renderValidate();
    await submitIdea();
    expect(await screen.findByText("Competition scorer", { selector: "strong" })).toBeInTheDocument();
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(body).toEqual({ title: "Notes exporter", idea_text: "A CLI that exports Notion pages to clean Markdown.", idea_id: null });
    const tools = await screen.findByRole("list", { name: "Tool calls" });
    expect(within(tools).getByText("kb.search")).toBeInTheDocument();
    expect(within(tools).getByText("cache")).toBeInTheDocument();
    expect(screen.getByText(/\$0\.042 \/ \$0\.050/)).toHaveClass("text-amber-600"); // >= 80% of the budget
    expect(screen.getByRole("progressbar", { name: "Budget used" })).toHaveAttribute("aria-valuenow", "84");
  });

  it("asks a numeric question, validates the range and posts the answer", async () => {
    let answered: unknown;
    server.use(
      http.post(`${API}/validate`, () => HttpResponse.json({ run_id: "run1", idea_id: "i", version: 1 }, { status: 202 })),
      http.get(`${API}/runs/run1/events`, () =>
        sse(
          frame(1, "run_started", { kind: "validate" }),
          frame(2, "question", {
            question_id: "q_estimated_effort_hours",
            text: "About how many hours will building this take you?",
            range: [0, 400],
            required: true,
            why_it_matters: "GO if 100 h, NO-GO if 300 h.",
          }),
          frame(3, "state_changed", { from: "running", to: "awaiting_input" }),
        ),
      ),
      http.post(`${API}/runs/run1/answer`, async ({ request }) => {
        answered = await request.json();
        return HttpResponse.json({ run_id: "run1", state: "running" }, { status: 202 });
      }),
    );
    renderValidate();
    await submitIdea();
    const input = await screen.findByLabelText(/how many hours/i);
    expect(screen.getByText(/why we ask: go if 100 h/i)).toBeInTheDocument();
    await userEvent.type(input, "900");
    await userEvent.click(screen.getByRole("button", { name: /answer/i }));
    expect(await screen.findByText("Enter a number between 0 and 400")).toBeInTheDocument();
    expect(answered).toBeUndefined();
    await userEvent.clear(input);
    await userEvent.type(input, "120");
    await userEvent.click(screen.getByRole("button", { name: /answer/i }));
    await waitFor(() => expect(answered).toEqual({ question_id: "q_estimated_effort_hours", answer: 120 }));
  });

  it("shows the approval dialog and sends the user's decision, nothing before", async () => {
    let decision: unknown;
    server.use(
      http.post(`${API}/validate`, () => HttpResponse.json({ run_id: "run1", idea_id: "i", version: 1 }, { status: 202 })),
      http.get(`${API}/runs/run1/events`, () =>
        sse(
          frame(1, "run_started", { kind: "validate" }),
          frame(2, "approval_required", {
            approval_id: "a1",
            action: "deep_search",
            description: "Deep search",
            estimated_cost_usd: 0.03,
            spent_usd: 0.04,
            budget_usd: 0.05,
          }),
        ),
      ),
      http.post(`${API}/runs/run1/approve`, async ({ request }) => {
        decision = await request.json();
        return HttpResponse.json({ run_id: "run1", state: "running" }, { status: 202 });
      }),
    );
    renderValidate();
    await submitIdea();
    const dialog = await screen.findByRole("dialog", { name: /approval needed/i });
    expect(within(dialog).getByText(/\$0\.03/)).toBeInTheDocument();
    expect(decision).toBeUndefined();
    await userEvent.click(within(dialog).getByRole("button", { name: "Decline" }));
    await waitFor(() => expect(decision).toEqual({ approval_id: "a1", decision: "decline" }));
  });

  it("explains a budget stop and a failure in plain words", async () => {
    server.use(
      http.post(`${API}/validate`, () => HttpResponse.json({ run_id: "run1", idea_id: "i", version: 1 }, { status: 202 })),
      http.get(`${API}/runs/run1/events`, () => sse(frame(1, "run_started", {}), frame(2, "budget_stopped", { reason: "token budget" }))),
    );
    renderValidate();
    await submitIdea();
    expect(await screen.findByRole("alert")).toHaveTextContent(/stopped because it reached its budget/i);
  });

  it("shows a plain error and keeps the form when the gateway rejects the start", async () => {
    server.use(
      http.post(`${API}/validate`, () =>
        HttpResponse.json({ error: { code: "RATE_LIMITED", message: "slow" } }, { status: 429, headers: { "Retry-After": "30" } }),
      ),
    );
    renderValidate();
    await submitIdea();
    expect(await screen.findByRole("alert")).toHaveTextContent("wait 30 seconds");
    expect(screen.getByLabelText("Title")).toHaveValue("Notes exporter");
  });

  it("never shows a verdict of its own: a finished run only says the result is ready", async () => {
    server.use(
      http.post(`${API}/validate`, () => HttpResponse.json({ run_id: "run1", idea_id: "i", version: 1 }, { status: 202 })),
      http.get(`${API}/runs/run1/events`, () =>
        sse(frame(1, "run_started", {}), frame(2, "verdict", { verdict: "GO", score: 80 }), frame(3, "run_completed", {})),
      ),
    );
    renderValidate();
    await submitIdea();
    expect(await screen.findByText(/your result is ready/i)).toBeInTheDocument();
    expect(screen.queryByText("GO")).not.toBeInTheDocument();
  });
});
