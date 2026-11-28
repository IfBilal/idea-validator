import { z } from "zod";
import { apiFetch } from "./api";

const Totals = z.object({
  steps: z.number(),
  tokens_in: z.number(),
  tokens_out: z.number(),
  cost_usd: z.number(),
  latency_ms: z.number(),
  errors: z.number(),
});
const RunTotals = Totals.extend({
  started_at: z.string().nullable(),
  ended_at: z.string().nullable(),
  wall_ms: z.number(),
});

// input/output are deliberately not parsed: the viewer never shows them.
const StepRecord = z.object({
  step_id: z.number(),
  parent_step: z.number().nullable(),
  agent: z.string(),
  kind: z.enum(["tool_call", "llm_call", "decision"]),
  tool: z.string().nullable(),
  model: z.string().nullable(),
  tokens_in: z.number(),
  tokens_out: z.number(),
  cost_usd: z.number(),
  latency_ms: z.number(),
  error: z.string().nullable(),
});

export type TraceNode = { record_id: string; record: z.infer<typeof StepRecord>; children: TraceNode[]; orphan: boolean };
const TraceNodeSchema: z.ZodType<TraceNode> = z.lazy(() =>
  z.object({ record_id: z.string(), record: StepRecord, children: z.array(TraceNodeSchema), orphan: z.boolean() }),
);

const TraceSchema = z.object({
  run_id: z.string(),
  totals: RunTotals,
  by_agent: z.record(z.string(), Totals),
  truncated: z.boolean(),
  tree: z.array(TraceNodeSchema),
});
export type Trace = z.infer<typeof TraceSchema>;

/** null when the payload is not the tree shape of M13's TraceTree: callers show a clear failure. */
export const parseTrace = (raw: unknown): Trace | null => {
  const r = TraceSchema.safeParse(raw);
  return r.success ? r.data : null;
};

/** The gateway checks ownership: another user's or unknown run is the same 404. A different run id fails closed. */
export const getTrace = (runId: string) =>
  apiFetch(`/runs/${encodeURIComponent(runId)}/trace`, TraceSchema.refine((t) => t.run_id === runId));
