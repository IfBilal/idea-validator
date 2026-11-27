import { describe, expect, it } from "vitest";
import { initialRunState, isTerminal, runReducer, type RunState } from "@/lib/runState";
import type { RunEvent } from "@/lib/sse";

let seq = 0;
const ev = (type: RunEvent["type"], payload: Record<string, unknown> = {}, at?: number): RunEvent => ({
  run_id: "r1",
  seq: at ?? ++seq,
  ts: "2026-01-01T00:00:00Z",
  type,
  payload,
});
const fold = (events: RunEvent[], from: RunState = initialRunState) => events.reduce(runReducer, from);

describe("runReducer", () => {
  it("tracks the lifecycle of a validate run", () => {
    seq = 0;
    const s = fold([
      ev("run_started", { kind: "validate", budget: { cost_usd: 0.05, tokens: 20000 } }),
      ev("agent_started", { agent: "Demand scorer", step_id: "s1" }),
      ev("agent_started", { agent: "Competition scorer", step_id: "s2" }),
      ev("agent_finished", { agent: "Demand scorer", step_id: "s1" }),
    ]);
    expect(s.phase).toBe("running");
    expect(s.kind).toBe("validate");
    expect(s.budget).toEqual({ cost_usd: 0.05, tokens: 20000 });
    expect(s.currentAgent).toBe("Competition scorer");
    expect(s.steps.map((x) => [x.agent, x.status])).toEqual([
      ["Demand scorer", "done"],
      ["Competition scorer", "running"],
    ]);
    expect(isTerminal(s.phase)).toBe(false);
  });

  it("ignores replayed and duplicate events and heartbeats", () => {
    seq = 0;
    const started = ev("run_started", { kind: "validate" });
    const once = fold([started]);
    expect(fold([started], once)).toBe(once);
    expect(runReducer(once, ev("heartbeat", {}, 99))).toBe(once);
    const tool = ev("tool_call", { tool: "kb.search" });
    expect(fold([tool, tool], once).toolCalls).toHaveLength(1);
  });

  it("bounds the tool call feed", () => {
    seq = 0;
    const events = Array.from({ length: 260 }, () => ev("tool_call", { tool: "t", cache_hit: true }));
    const s = fold(events);
    expect(s.toolCalls).toHaveLength(200);
    expect(s.toolCalls.at(-1)?.cacheHit).toBe(true);
  });

  it("shows a numeric question with its range and why, then clears it when the run resumes", () => {
    seq = 0;
    const asked = fold([
      ev("question", {
        question_id: "q_hours",
        field: "estimated_effort_hours",
        text: "How many hours?",
        range: [0, 400],
        required: true,
        why_it_matters: "GO if 100 h, NO-GO if 300 h.",
      }),
      ev("state_changed", { from: "running", to: "awaiting_input" }),
    ]);
    expect(asked.phase).toBe("waiting_answer");
    expect(asked.question).toMatchObject({ questionId: "q_hours", range: [0, 400], required: true });
    const resumed = fold([ev("state_changed", { from: "awaiting_input", to: "running" })], asked);
    expect(resumed.phase).toBe("running");
    expect(resumed.question).toBeUndefined();
  });

  it("ignores a malformed range and a question without an id", () => {
    seq = 0;
    expect(fold([ev("question", { question_id: "q", range: ["a", 1], text: "t" })]).question?.range).toBeUndefined();
    expect(fold([ev("question", { text: "no id" })]).question).toBeUndefined();
  });

  it("holds an approval request until the run moves on", () => {
    seq = 0;
    const s = fold([
      ev("approval_required", {
        approval_id: "a1",
        action: "deep_search",
        estimated_cost_usd: 0.03,
        spent_usd: 0.04,
        budget_usd: 0.05,
        description: "Fetch ~20 more pages",
      }),
    ]);
    expect(s.phase).toBe("waiting_approval");
    expect(s.approval).toMatchObject({ approvalId: "a1", estimatedCostUsd: 0.03 });
    expect(fold([ev("run_completed", {})], s).approval).toBeUndefined();
  });

  it("updates the spend meter from spend_update", () => {
    seq = 0;
    const s = fold([ev("spend_update", { spent: { cost_usd: 0.012, tokens: 900 }, budget: { cost_usd: 0.05 } })]);
    expect(s.spent).toEqual({ cost_usd: 0.012, tokens: 900 });
    expect(s.budget).toEqual({ cost_usd: 0.05 });
  });

  it("collects Discover survivors and dropped ideas", () => {
    seq = 0;
    const s = fold([
      ev("partial_result", { idea_id: "i1", verdict: "GO", score: 72.5 }),
      ev("idea_dropped", { title: "Another todo app", round: 2 }),
    ]);
    expect(s.partials).toEqual([{ idea_id: "i1", verdict: "GO", score: 72.5 }]);
    expect(s.dropped).toEqual([{ title: "Another todo app", round: 2 }]);
  });

  it("keeps the verdict card verbatim and never computes a verdict itself", () => {
    seq = 0;
    const card = { verdict: "PIVOT", score: 61.2, partial: false };
    const s = fold([ev("verdict", card), ev("run_completed", {})]);
    expect(s.verdictCard).toEqual(card);
    expect(s.phase).toBe("done");
  });

  it.each([
    ["run_failed", "failed"],
    ["budget_stopped", "stopped"],
    ["run_completed", "done"],
  ] as const)("%s ends the run as %s and is terminal", (type, phase) => {
    seq = 0;
    const s = fold([ev("agent_started", { agent: "x" }), ev(type, { reason: "why" })]);
    expect(s.phase).toBe(phase);
    expect(s.currentAgent).toBeUndefined();
    expect(isTerminal(s.phase)).toBe(true);
  });

  it("tolerates wrong payload types without throwing", () => {
    seq = 0;
    expect(() =>
      fold([ev("tool_call", { tool: 5, latency_ms: "fast" }), ev("spend_update", { spent: "lots" }), ev("agent_started", {})]),
    ).not.toThrow();
  });
});
