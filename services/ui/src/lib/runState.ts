import type { RunEvent } from "./sse";

export type RunPhase =
  | "starting"
  | "running"
  | "waiting_answer"
  | "waiting_approval"
  | "done"
  | "failed"
  | "stopped";

export interface Step {
  stepId: string;
  agent: string;
  round?: number;
  status: "running" | "done";
}

export interface ToolCall {
  seq: number;
  agent: string;
  tool: string;
  summary: string;
  latencyMs?: number;
  cacheHit: boolean;
  error?: string;
}

export interface Spend {
  cost_usd: number;
  tokens: number;
  pages: number;
  steps: number;
}

/** M3 asks numeric questions only (hours, team size...): a number within `range`. */
export interface Question {
  questionId: string;
  field?: string;
  text: string;
  range?: [number, number];
  required: boolean;
  why?: string;
}

export interface Approval {
  approvalId: string;
  action: string;
  description: string;
  estimatedCostUsd: number;
  spentUsd: number;
  budgetUsd: number;
}

export interface RunState {
  phase: RunPhase;
  kind?: string;
  lastSeq: number;
  currentAgent?: string;
  steps: Step[];
  toolCalls: ToolCall[];
  spent?: Partial<Spend>;
  budget?: Partial<Spend>;
  round?: number;
  question?: Question;
  approval?: Approval;
  verdictCard?: unknown;
  partials: { idea_id: string; verdict: string | null; score: number | null }[];
  dropped: { title: string; round?: number }[];
  reason?: string;
}

export const initialRunState: RunState = {
  phase: "starting",
  lastSeq: -1,
  steps: [],
  toolCalls: [],
  partials: [],
  dropped: [],
};

const MAX_TOOL_CALLS = 200; // the feed is bounded: a long run must not grow the page without limit

const str = (v: unknown): string | undefined => (typeof v === "string" ? v : undefined);
const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const rec = (v: unknown): Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

function counters(v: unknown): Partial<Spend> {
  const r = rec(v);
  const out: Partial<Spend> = {};
  for (const key of ["cost_usd", "tokens", "pages", "steps"] as const) {
    const n = num(r[key]);
    if (n !== undefined) out[key] = n;
  }
  return out;
}

/** Fold one event into the run state. Pure; duplicates and replays (seq <= lastSeq) are ignored. */
export function runReducer(state: RunState, event: RunEvent): RunState {
  if (event.type === "heartbeat") return state;
  if (event.seq <= state.lastSeq) return state;
  const p = event.payload;
  const next: RunState = { ...state, lastSeq: event.seq };

  switch (event.type) {
    case "run_started":
      return { ...next, phase: "running", kind: str(p.kind), budget: counters(p.budget) };
    case "state_changed": {
      const to = str(p.to);
      if (to === "awaiting_input") return { ...next, phase: "waiting_answer" };
      if (to === "awaiting_approval") return { ...next, phase: "waiting_approval" };
      if (to === "running") return { ...next, phase: "running", question: undefined, approval: undefined };
      return next;
    }
    case "agent_started": {
      const agent = str(p.agent) ?? "agent";
      const stepId = str(p.step_id) ?? `${agent}-${event.seq}`;
      const round = num(p.round);
      const steps = [...state.steps.filter((s) => s.stepId !== stepId), { stepId, agent, round, status: "running" as const }];
      return { ...next, currentAgent: agent, steps, round: round ?? state.round };
    }
    case "agent_finished": {
      const stepId = str(p.step_id);
      const agent = str(p.agent);
      const steps = state.steps.map((s) =>
        (stepId !== undefined ? s.stepId === stepId : s.agent === agent && s.status === "running")
          ? { ...s, status: "done" as const }
          : s,
      );
      const stillRunning = steps.filter((s) => s.status === "running");
      return { ...next, steps, currentAgent: stillRunning.at(-1)?.agent };
    }
    case "tool_call": {
      const call: ToolCall = {
        seq: event.seq,
        agent: str(p.agent) ?? "",
        tool: str(p.tool) ?? "tool",
        summary: str(p.input_summary) ?? "",
        latencyMs: num(p.latency_ms),
        cacheHit: p.cache_hit === true,
        error: str(p.error),
      };
      return { ...next, toolCalls: [...state.toolCalls, call].slice(-MAX_TOOL_CALLS) };
    }
    case "spend_update":
      return { ...next, spent: counters(p.spent), budget: { ...state.budget, ...counters(p.budget) } };
    case "question": {
      const questionId = str(p.question_id);
      if (!questionId) return next;
      const r = Array.isArray(p.range) && p.range.length === 2 ? p.range.map(num) : [];
      const range = r[0] !== undefined && r[1] !== undefined ? ([r[0], r[1]] as [number, number]) : undefined;
      return {
        ...next,
        phase: "waiting_answer",
        question: {
          questionId,
          field: str(p.field),
          text: str(p.text) ?? "",
          range,
          required: p.required === true,
          why: str(p.why_it_matters),
        },
      };
    }
    case "approval_required": {
      const approvalId = str(p.approval_id);
      if (!approvalId) return next;
      return {
        ...next,
        phase: "waiting_approval",
        approval: {
          approvalId,
          action: str(p.action) ?? "",
          description: str(p.description) ?? "",
          estimatedCostUsd: num(p.estimated_cost_usd) ?? 0,
          spentUsd: num(p.spent_usd) ?? 0,
          budgetUsd: num(p.budget_usd) ?? 0,
        },
      };
    }
    case "idea_dropped":
      return { ...next, dropped: [...state.dropped, { title: str(p.title) ?? "Untitled", round: num(p.round) }] };
    case "partial_result":
      return {
        ...next,
        partials: [
          ...state.partials,
          { idea_id: str(p.idea_id) ?? "", verdict: str(p.verdict) ?? null, score: num(p.score) ?? null },
        ],
      };
    case "verdict":
      return { ...next, verdictCard: p, question: undefined, approval: undefined };
    case "run_completed":
      return { ...next, phase: "done", currentAgent: undefined, question: undefined, approval: undefined };
    case "run_failed":
      return { ...next, phase: "failed", reason: str(p.reason), currentAgent: undefined };
    case "budget_stopped":
      return { ...next, phase: "stopped", reason: str(p.reason), currentAgent: undefined };
    default:
      return next;
  }
}

export const isTerminal = (phase: RunPhase): boolean => phase === "done" || phase === "failed" || phase === "stopped";
