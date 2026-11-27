import { z } from "zod";

/**
 * The UI only renders what M11 and M3 produced: it never computes, rounds, ranks or edits a verdict.
 * The settled-card schema is derived from schemas/verdict.schema.json (the public M11 Verdict) plus the
 * extras M3 adds to a validate result (claims, evidence, notes, cache_hit, challenge_log, ...).
 * Anything contradictory fails closed (see `parseCard`).
 */
export const VERDICTS = ["GO", "PIVOT", "NO-GO", "INSUFFICIENT_EVIDENCE"] as const;
export type VerdictLabel = (typeof VERDICTS)[number];

export const DIMENSIONS = [
  "demand_pain",
  "competition",
  "differentiation",
  "feasibility",
  "risk",
  "learning",
] as const;
export type Dimension = (typeof DIMENSIONS)[number];

export const DIMENSION_LABEL: Record<Dimension, string> = {
  demand_pain: "Demand & pain",
  competition: "Competition",
  differentiation: "Differentiation",
  feasibility: "Feasibility",
  risk: "Risk",
  learning: "Learning value",
};

export const GATES = ["evidence", "feasibility", "risk", "duplicate"] as const;
export const GATE_LABEL: Record<string, string> = {
  evidence: "Evidence",
  feasibility: "Feasibility",
  risk: "Risk",
  duplicate: "Duplicate",
};
const GateState = z.enum(["pass", "fail", "not_evaluated"]);
export type GateStateValue = z.infer<typeof GateState>;

const DimensionScore = z
  .object({
    dimension: z.string(),
    score: z.number().nullable(),
    confidence: z.number().nullable().optional(),
    evidence_ids: z.array(z.string()).default([]),
    reason: z.string().nullable().optional(),
    abstained: z.boolean().nullable().optional(),
    adjusted_by_challenge: z.number().nullable().optional(),
  })
  .passthrough();
export type DimensionScore = z.infer<typeof DimensionScore>;

const Claim = z
  .object({
    claim_id: z.string().optional(),
    text: z.string(),
    evidence_ids: z.array(z.string()).default([]),
    dimension: z.string().optional(),
    supported: z.boolean().nullable().optional(),
    removed: z.boolean().nullable().optional(),
  })
  .passthrough();
export type Claim = z.infer<typeof Claim>;

const EvidenceItem = z
  .object({
    url: z.string().nullable().optional(),
    title: z.string().nullable().optional(),
    quote: z.string().nullable().optional(),
    text: z.string().nullable().optional(),
  })
  .passthrough();

const WhatWouldMakeGo = z
  .object({
    change: z.string().nullable().optional(),
    dimension: z.string().nullable().optional(),
    from: z.number().nullable().optional(),
    to: z.number().nullable().optional(),
    new_score: z.number().nullable().optional(),
    new_verdict: z.string(),
    stable: z.boolean().nullable().optional(),
  })
  .passthrough();

const Settled = z
  .object({
    idea_id: z.string().optional(),
    version: z.number().int().optional(),
    run_id: z.string().optional(),
    verdict: z.enum(VERDICTS),
    partial: z.boolean().nullable().optional(),
    stability: z.enum(["STABLE", "BORDERLINE"]).nullable().optional(),
    score: z.number().nullable(),
    confidence: z.number().nullable(),
    gates: z.object({ evidence: GateState, feasibility: GateState, risk: GateState, duplicate: GateState }),
    weights: z.record(z.string(), z.number()).default({}),
    thresholds: z.object({ T_go: z.number(), T_no: z.number(), floor: z.number() }),
    scores: z.array(DimensionScore).default([]),
    weakest_dimension: z.string().nullable().optional(),
    what_would_make_go: z.array(WhatWouldMakeGo).default([]),
    sensitivity: z
      .object({
        runs: z.number(),
        flipped: z.number(),
        flips: z.array(z.record(z.string(), z.unknown())).nullable().optional(),
        note: z.string().nullable().optional(),
      })
      .nullable()
      .optional(),
    reasons: z.array(z.string()).nullable().optional(),
    missing: z.array(z.string()).nullable().optional(),
    versions: z.record(z.string(), z.string()).nullable().optional(),
    // M3 additions to a validate result
    claims: z.array(Claim).default([]),
    evidence: z.record(z.string(), EvidenceItem).default({}),
    notes: z.array(z.string()).default([]),
    cache_hit: z.boolean().nullable().optional(),
  })
  .passthrough()
  .superRefine((card, ctx) => {
    const fail = (message: string) => ctx.addIssue({ code: "custom", message });
    const failed = Object.values(card.gates).some((g) => g === "fail");
    if (card.verdict === "GO" || card.verdict === "PIVOT") {
      if (!card.stability) fail(`${card.verdict} without stability`);
      if (card.score === null) fail(`${card.verdict} without a score`);
    }
    if (card.verdict === "GO" && failed) fail("GO with a failed gate");
    // NO-GO comes from a failed gate or from a score below the threshold: it needs one of the two
    if (card.verdict === "NO-GO" && !failed && card.score === null) fail("NO-GO with neither a failed gate nor a score");
    if (card.partial === true) fail("a verdict together with partial: true");
  });

const Partial = z
  .object({
    verdict: z.null(),
    partial: z.literal(true),
    idea_id: z.string().optional(),
    version: z.number().int().optional(),
    needs_input: z.array(z.string()).default([]),
    missing: z.array(z.string()).nullable().optional(),
    stop_reason: z.string().nullable().optional(),
    error: z.string().nullable().optional(),
    degraded: z.boolean().nullable().optional(),
    notes: z.array(z.string()).default([]),
    // none of the M11-derived fields may be present on a card without a verdict
    score: z.null().optional(),
    confidence: z.null().optional(),
    stability: z.null().optional(),
  })
  .passthrough();

export type SettledCard = z.infer<typeof Settled>;
export type PartialCard = z.infer<typeof Partial>;
export type ParsedCard =
  | { kind: "settled"; card: SettledCard }
  | { kind: "partial"; card: PartialCard }
  | { kind: "invalid" };

/**
 * Fail closed. Only a clearly valid settled card is a verdict, and only an explicit
 * `{verdict: null, partial: true}` is a "no verdict" card. Everything else (missing verdict, a verdict
 * together with partial, a score without a verdict, contradictory fields, garbage) is `invalid`
 * and shows an error, never a verdict.
 */
export function parseCard(raw: unknown): ParsedCard {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return { kind: "invalid" };
  const verdict = (raw as { verdict?: unknown }).verdict;
  if (verdict === null) {
    const p = Partial.safeParse(raw);
    return p.success ? { kind: "partial", card: p.data } : { kind: "invalid" };
  }
  const s = Settled.safeParse(raw);
  return s.success ? { kind: "settled", card: s.data } : { kind: "invalid" };
}

/** Evidence links open the source only if it is a plain http(s) URL (no javascript:, data:, ...). */
export function safeHref(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : undefined;
  } catch {
    return undefined;
  }
}

export const MISSING_TEXT = (m: string): string => m.replace(/_/g, " ");
