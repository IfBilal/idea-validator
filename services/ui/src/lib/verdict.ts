import { z } from "zod";

/** The UI only renders what M11/M3 produced: it never computes, rounds or edits a verdict. */
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

export const GATE_LABEL: Record<string, string> = {
  evidence: "Evidence",
  feasibility: "Feasibility",
  risk: "Risk",
  duplicate: "Duplicate",
};

const Claim = z.object({
  claim_id: z.string().optional(),
  text: z.string(),
  evidence_ids: z.array(z.string()).default([]),
  dimension: z.string().optional(),
  supported: z.boolean().optional(),
  removed: z.boolean().optional(),
});
export type Claim = z.infer<typeof Claim>;

const EvidenceItem = z
  .object({ url: z.string().optional(), title: z.string().optional(), quote: z.string().optional(), text: z.string().optional() })
  .passthrough();

const Common = {
  idea_id: z.string().optional(),
  version: z.number().int().optional(),
  notes: z.array(z.string()).default([]),
  cache_hit: z.boolean().optional(),
};

const SettledCard = z
  .object({
    ...Common,
    verdict: z.enum(VERDICTS),
    stability: z.enum(["STABLE", "BORDERLINE"]).optional(),
    score: z.number(),
    confidence: z.number(),
    gates: z.record(z.string(), z.string()).default({}),
    weights: z.record(z.string(), z.number()).default({}),
    thresholds: z.object({ T_go: z.number(), T_no: z.number(), floor: z.number() }).partial().optional(),
    reasons: z.array(z.string()).default([]),
    weakest_dimension: z.string().nullable().optional(),
    adjusted_scores: z.record(z.string(), z.number().nullable()).default({}),
    sensitivity: z
      .object({
        runs: z.number(),
        flipped: z.number(),
        flips: z.array(z.object({ change: z.string(), verdict: z.string(), score: z.number() })).default([]),
      })
      .optional(),
    what_would_make_go: z
      .array(
        z.object({
          dimension: z.string(),
          from: z.number().optional(),
          to: z.number().optional(),
          new_score: z.number().optional(),
          new_verdict: z.string().optional(),
          stable: z.boolean().optional(),
        }),
      )
      .default([]),
    versions: z.record(z.string(), z.string()).optional(),
    claims: z.array(Claim).default([]),
    missing: z.array(z.string()).default([]),
    evidence: z.record(z.string(), EvidenceItem).default({}),
    confidence_by_dimension: z.record(z.string(), z.number()).optional(),
    evidence_counts: z.record(z.string(), z.number()).optional(),
  })
  .passthrough();

const PartialCard = z
  .object({
    ...Common,
    verdict: z.null(),
    partial: z.literal(true),
    needs_input: z.array(z.string()).default([]),
    missing: z.array(z.string()).default([]),
    stop_reason: z.string().optional(),
    error: z.string().optional(),
    degraded: z.boolean().optional(),
    scores: z.record(z.string(), z.unknown()).optional(),
  })
  .passthrough();

export type SettledCard = z.infer<typeof SettledCard>;
export type PartialCard = z.infer<typeof PartialCard>;
export type ParsedCard = { kind: "settled"; card: SettledCard } | { kind: "partial"; card: PartialCard } | { kind: "invalid" };

/** Anything that is not clearly a settled verdict is rendered as "no verdict" (fail safe). */
export function parseCard(raw: unknown): ParsedCard {
  if (typeof raw === "object" && raw !== null) {
    const verdict = (raw as { verdict?: unknown }).verdict;
    const partial = (raw as { partial?: unknown }).partial === true;
    if (verdict === null || verdict === undefined || partial) {
      const p = PartialCard.safeParse({ ...raw, verdict: null, partial: true });
      return p.success ? { kind: "partial", card: p.data } : { kind: "invalid" };
    }
    const s = SettledCard.safeParse(raw);
    if (s.success) return { kind: "settled", card: s.data };
  }
  return { kind: "invalid" };
}

/** Evidence links open the source only if it is a plain http(s) URL (no javascript:, data:, ...). */
export function safeHref(url: string | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : undefined;
  } catch {
    return undefined;
  }
}

export const MISSING_TEXT = (m: string): string => m.replace(/_/g, " ");
