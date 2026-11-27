import { z } from "zod";
import { parseCard, type ParsedCard } from "./verdict";

/** Result of a challenge run (docs/harness/05): defended, or the claim was removed and a new version saved. */
const ChallengeResult = z
  .object({
    claim_id: z.string(),
    outcome: z.enum(["defended", "removed"]).optional(),
    reason: z.string().nullable().optional(),
    evidence_ids: z.array(z.string()).default([]),
    supporting_quote: z.string().nullable().optional(),
    new_version: z.number().int().optional(),
    card: z.unknown().optional(),
    already_removed: z.boolean().optional(),
    partial: z.boolean().optional(),
    stop_reason: z.string().nullable().optional(),
    error: z.string().nullable().optional(),
  })
  .passthrough();

export type ChallengeOutcome =
  | { kind: "defended"; reason: string; quote?: string; evidenceIds: string[] }
  | { kind: "removed"; reason: string; newVersion: number; card: ParsedCard; alreadyRemoved: boolean }
  | { kind: "stopped"; reason?: string }
  | { kind: "invalid" };

/** Fail closed: anything that is not a clear defended/removed answer is "stopped" or "invalid", never an outcome. */
export function parseChallengeResult(raw: unknown): ChallengeOutcome {
  const parsed = ChallengeResult.safeParse(raw);
  if (!parsed.success) return { kind: "invalid" };
  const r = parsed.data;
  if (r.partial === true || r.outcome === undefined) {
    return { kind: "stopped", reason: r.stop_reason ?? r.error ?? undefined };
  }
  if (r.outcome === "defended") {
    return { kind: "defended", reason: r.reason ?? "", quote: r.supporting_quote ?? undefined, evidenceIds: r.evidence_ids };
  }
  // removed: a new version and its card must both be present, otherwise nothing is claimed
  if (r.new_version === undefined || r.card === undefined) {
    return r.already_removed
      ? { kind: "removed", reason: r.reason ?? "", newVersion: 0, card: { kind: "invalid" }, alreadyRemoved: true }
      : { kind: "invalid" };
  }
  return { kind: "removed", reason: r.reason ?? "", newVersion: r.new_version, card: parseCard(r.card), alreadyRemoved: r.already_removed === true };
}

const DimensionDiff = z.object({ dimension: z.string(), from: z.number(), to: z.number(), delta: z.number() });
export const WhatIfDiff = z
  .object({
    verdict_from: z.string().nullable().optional(),
    verdict_to: z.string().nullable().optional(),
    verdict_changed: z.boolean().optional(),
    score_from: z.number().nullable().optional(),
    score_to: z.number().nullable().optional(),
    dimensions: z.array(DimensionDiff).default([]),
  })
  .passthrough();
export type WhatIfDiff = z.infer<typeof WhatIfDiff>;

export interface WhatIfOutcome {
  card: ParsedCard;
  version?: number;
  baseVersion?: number;
  changeNote?: string;
  diff?: WhatIfDiff;
}

/** A what-if result is the verdict card of the next version plus base_version, change_note and diff. */
export function parseWhatIfResult(raw: unknown): WhatIfOutcome {
  const card = parseCard(raw);
  const r = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const diff = WhatIfDiff.safeParse(r.diff);
  return {
    card,
    version: typeof r.version === "number" ? r.version : undefined,
    baseVersion: typeof r.base_version === "number" ? r.base_version : undefined,
    changeNote: typeof r.change_note === "string" ? r.change_note : undefined,
    diff: diff.success ? diff.data : undefined,
  };
}
