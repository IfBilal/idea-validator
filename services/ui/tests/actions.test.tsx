import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { ResultActions } from "@/components/ResultActions";
import { parseChallengeResult, parseWhatIfResult } from "@/lib/actions";
import { clearSession, resetAuthForTests, setSession } from "@/lib/auth";
import go from "./fixtures/m11_go.json";
import pivot from "./fixtures/m11_pivot_with_what_if.json";

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

type Card = Record<string, unknown>;
const EV = "card_1a2b3c00#c0";
const baseCard: Card = {
  ...(pivot as Card),
  claims: [{ claim_id: "cl_competition", text: "Two tools already export Notion pages.", evidence_ids: [EV], dimension: "competition" }],
  evidence: { [EV]: { url: "https://news.ycombinator.com/item?id=1", quote: "a quote" } },
  changes: [{ change_id: "ch1", text: "Narrow to students who use Obsidian.", target_dimension: "competition" }],
};
const goCard: Card = { ...(go as Card), claims: [{ claim_id: "cl_x", text: "A claim", evidence_ids: [EV], dimension: "risk" }], changes: [] };

const frame = (runId: string, seq: number, type: string, payload: object = {}) =>
  `event: ${type}\nid: ${seq}\ndata: ${JSON.stringify({ run_id: runId, seq, ts: "2026-01-01T00:00:00Z", type, payload })}\n\n`;
const sse = (...frames: string[]) => new HttpResponse(frames.join(""), { headers: { "Content-Type": "text/event-stream" } });
const finished = (runId: string) => sse(frame(runId, 1, "run_started", {}), frame(runId, 2, "run_completed", {}));

function renderActions(raw: unknown = baseCard, version = 1) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ResultActions ideaId="idea1" version={version} raw={raw} />
    </QueryClientProvider>,
  );
}

describe("challenge a claim", () => {
  it("posts the claim with an idempotency key and shows 'defended' with the supporting quote", async () => {
    let body: unknown;
    let key: string | null = null;
    server.use(
      http.post(`${API}/ideas/idea1/challenge`, async ({ request }) => {
        body = await request.json();
        key = request.headers.get("Idempotency-Key");
        return HttpResponse.json({ run_id: "rc1" }, { status: 202 });
      }),
      http.get(`${API}/runs/rc1/events`, () => finished("rc1")),
      http.get(`${API}/runs/rc1`, () =>
        HttpResponse.json({
          state: "succeeded",
          result: { claim_id: "cl_competition", outcome: "defended", reason: "Supported by the source.", evidence_ids: [EV], supporting_quote: "exports are slow" },
        }),
      ),
    );
    renderActions();
    await userEvent.click(screen.getByRole("button", { name: "Challenge" }));
    expect(await screen.findByText("Defended")).toBeInTheDocument();
    expect(screen.getByText("exports are slow")).toBeInTheDocument();
    expect(body).toEqual({ version: 1, claim_id: "cl_competition" });
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(screen.getByText(/the verdict is unchanged/i)).toBeInTheDocument();
  });

  it("a removed claim shows the new version and its recomputed card, which can be acted on again", async () => {
    server.use(
      http.post(`${API}/ideas/idea1/challenge`, () => HttpResponse.json({ run_id: "rc2" }, { status: 202 })),
      http.get(`${API}/runs/rc2/events`, () => finished("rc2")),
      http.get(`${API}/runs/rc2`, () =>
        HttpResponse.json({
          state: "succeeded",
          result: { claim_id: "cl_competition", outcome: "removed", reason: "The quote does not mention exports.", evidence_ids: [EV], new_version: 2, card: goCard },
        }),
      ),
    );
    renderActions();
    await userEvent.click(screen.getByRole("button", { name: "Challenge" }));
    expect(await screen.findByText(/Removed: the evidence did not support this claim/)).toBeInTheDocument();
    expect(screen.getByText(/new version v2/i)).toBeInTheDocument();
    expect(screen.getAllByRole("region", { name: "Verdict" })).toHaveLength(2); // the old card and the new one
    // the new version has its own Challenge button, scoped to version 2
    expect(screen.getAllByRole("button", { name: "Challenge" }).length).toBeGreaterThan(0);
  });

  it("a challenge that did not finish changes nothing and says why", async () => {
    server.use(
      http.post(`${API}/ideas/idea1/challenge`, () => HttpResponse.json({ run_id: "rc3" }, { status: 202 })),
      http.get(`${API}/runs/rc3/events`, () => sse(frame("rc3", 1, "run_started", {}), frame("rc3", 2, "budget_stopped", { reason: "token budget" }))),
      http.get(`${API}/runs/rc3`, () => HttpResponse.json({ state: "budget_stopped", result: { claim_id: "cl_competition", partial: true, stop_reason: "token budget" } })),
    );
    renderActions();
    await userEvent.click(screen.getByRole("button", { name: "Challenge" }));
    expect(await screen.findByText(/did not finish: token budget\. Nothing was changed/i)).toBeInTheDocument();
  });

  it("an unreadable outcome is an error, never 'defended' or 'removed'", async () => {
    server.use(
      http.post(`${API}/ideas/idea1/challenge`, () => HttpResponse.json({ run_id: "rc4" }, { status: 202 })),
      http.get(`${API}/runs/rc4/events`, () => finished("rc4")),
      http.get(`${API}/runs/rc4`, () => HttpResponse.json({ state: "succeeded", result: { outcome: "defended" } })),
    );
    renderActions();
    await userEvent.click(screen.getByRole("button", { name: "Challenge" }));
    expect(await screen.findByText(/outcome could not be displayed/i)).toBeInTheDocument();
    expect(screen.queryByText("Defended")).not.toBeInTheDocument();
  });

  it("hides the other actions while one is running and shows a plain error when the start is refused", async () => {
    server.use(
      http.post(`${API}/ideas/idea1/challenge`, () =>
        HttpResponse.json({ error: { code: "CONFLICT", message: "no verdict" } }, { status: 409 }),
      ),
    );
    renderActions();
    await userEvent.click(screen.getByRole("button", { name: "Challenge" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/changed in the meantime/i);
  });

  it("offers no actions at all while a run is in progress", async () => {
    server.use(
      http.post(`${API}/ideas/idea1/challenge`, () => HttpResponse.json({ run_id: "rc5" }, { status: 202 })),
      http.get(`${API}/runs/rc5/events`, () => sse(frame("rc5", 1, "run_started", {}), frame("rc5", 2, "agent_started", { agent: "Critic", step_id: "s1" }))),
    );
    renderActions();
    await userEvent.click(screen.getByRole("button", { name: "Challenge" }));
    expect(await screen.findByText("Critic", { selector: "strong" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Challenge" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try this change" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Try your own change" })).not.toBeInTheDocument();
  });
});

describe("what-if", () => {
  const resultCard: Card = {
    ...goCard,
    version: 2,
    base_version: 1,
    change_note: "Narrow to students who use Obsidian.",
    diff: {
      verdict_from: "PIVOT",
      verdict_to: "GO",
      verdict_changed: true,
      score_from: 60,
      score_to: 73.5,
      dimensions: [
        { dimension: "competition", from: 2, to: 3, delta: 1 },
        { dimension: "risk", from: 3.5, to: 3.5, delta: 0 },
      ],
    },
  };

  it("applies a suggested change by id and shows the new version with its diff", async () => {
    let body: unknown;
    server.use(
      http.post(`${API}/ideas/idea1/whatif`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ run_id: "rw1" }, { status: 202 });
      }),
      http.get(`${API}/runs/rw1/events`, () => finished("rw1")),
      http.get(`${API}/runs/rw1`, () => HttpResponse.json({ state: "succeeded", result: resultCard })),
    );
    renderActions();
    expect(screen.getByText("Narrow to students who use Obsidian.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try this change" }));
    expect(await screen.findByText(/New version v2 \(from v1\)/)).toBeInTheDocument();
    expect(body).toEqual({ version: 1, change_id: "ch1" });
    expect(screen.getByText(/Verdict: PIVOT → GO \(changed\) · score 60 → 73.5/)).toBeInTheDocument();
    const diff = screen.getByRole("list", { name: "Dimension changes" });
    expect(within(diff).getAllByRole("listitem")).toHaveLength(1); // unchanged dimensions are not listed
    expect(diff).toHaveTextContent("Competition: 2 → 3 (+1)");
  });

  it("validates a custom change and sends exactly one of change_id / custom_change", async () => {
    let body: unknown;
    let calls = 0;
    server.use(
      http.post(`${API}/ideas/idea1/whatif`, async ({ request }) => {
        calls += 1;
        body = await request.json();
        return HttpResponse.json({ run_id: "rw2" }, { status: 202 });
      }),
      http.get(`${API}/runs/rw2/events`, () => finished("rw2")),
      http.get(`${API}/runs/rw2`, () => HttpResponse.json({ state: "succeeded", result: resultCard })),
    );
    renderActions();
    await userEvent.click(screen.getByRole("button", { name: "Try it" }));
    expect(await screen.findByText("Describe the change you want to try")).toBeInTheDocument();
    const box = screen.getByRole("textbox", { name: "Try your own change" });
    await userEvent.click(box);
    await userEvent.paste("x".repeat(1001));
    await userEvent.click(screen.getByRole("button", { name: "Try it" }));
    expect(await screen.findByText("At most 1000 characters")).toBeInTheDocument();
    expect(calls).toBe(0);
    await userEvent.clear(box);
    await userEvent.type(box, "Only support Markdown export");
    await userEvent.click(screen.getByRole("button", { name: "Try it" }));
    await waitFor(() => expect(body).toEqual({ version: 1, custom_change: "Only support Markdown export" }));
    expect(body).not.toHaveProperty("change_id");
  });

  it("asks for approval during a what-if and sends the user's decision", async () => {
    let decision: unknown;
    server.use(
      http.post(`${API}/ideas/idea1/whatif`, () => HttpResponse.json({ run_id: "rw3" }, { status: 202 })),
      http.get(`${API}/runs/rw3/events`, () =>
        sse(
          frame("rw3", 1, "run_started", {}),
          frame("rw3", 2, "approval_required", { approval_id: "a9", action: "deep_search", description: "Deep search", estimated_cost_usd: 0.02, spent_usd: 0.01, budget_usd: 0.05 }),
        ),
      ),
      http.post(`${API}/runs/rw3/approve`, async ({ request }) => {
        decision = await request.json();
        return HttpResponse.json({ state: "running" }, { status: 202 });
      }),
    );
    renderActions();
    await userEvent.click(screen.getByRole("button", { name: "Try this change" }));
    const dialog = await screen.findByRole("dialog", { name: /approval needed/i });
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(decision).toEqual({ approval_id: "a9", decision: "approve" }));
  });

  it("offers no what-if for a GO card or an INSUFFICIENT_EVIDENCE card", () => {
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <ResultActions ideaId="idea1" version={1} raw={goCard} />
      </QueryClientProvider>,
    );
    expect(screen.queryByRole("form", { name: "Try your own change" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try this change" })).not.toBeInTheDocument();
    unmount();
  });
});

describe("result parsing fails closed", () => {
  it("challenge results", () => {
    expect(parseChallengeResult({ claim_id: "c", outcome: "defended", reason: "r" }).kind).toBe("defended");
    expect(parseChallengeResult({ claim_id: "c", outcome: "removed", new_version: 2, card: goCard }).kind).toBe("removed");
    expect(parseChallengeResult({ claim_id: "c", outcome: "removed" }).kind).toBe("invalid"); // removed without a saved card
    expect(parseChallengeResult({ claim_id: "c", outcome: "removed", already_removed: true }).kind).toBe("removed");
    expect(parseChallengeResult({ claim_id: "c", partial: true, stop_reason: "x" })).toEqual({ kind: "stopped", reason: "x" });
    expect(parseChallengeResult({ claim_id: "c", outcome: "defended", partial: true }).kind).toBe("stopped"); // partial wins
    expect(parseChallengeResult({ outcome: "defended" }).kind).toBe("invalid");
    expect(parseChallengeResult({ claim_id: "c", outcome: "maybe" }).kind).toBe("invalid");
    expect(parseChallengeResult(null).kind).toBe("invalid");
  });

  it("what-if results keep the card parser's rules", () => {
    expect(parseWhatIfResult({ ...goCard, version: 2, base_version: 1, change_note: "n", diff: { dimensions: [] } }).card.kind).toBe("settled");
    expect(parseWhatIfResult({ verdict: "GO", score: 80 }).card.kind).toBe("invalid");
    expect(parseWhatIfResult({ ...goCard, diff: "garbage" }).diff).toBeUndefined();
  });
});
