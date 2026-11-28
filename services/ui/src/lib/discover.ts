import { z } from "zod";
import { VERDICTS } from "./verdict";

/**
 * The result of a discover run (docs/harness/05 "Result for discover"). The UI shows the order, verdicts,
 * scores and confidence exactly as M3 returned them: it never ranks, rounds or fills in a value.
 * Anything contradictory fails closed (`parseDiscoverResult` returns null and the page shows an error).
 */
const Idea = z
  .object({
    idea_id: z.string().min(1),
    version: z.number().int().min(1),
    title: z.string(),
    summary: z.string().nullable().optional(),
    niche: z.string().nullable().optional(),
    verdict: z.enum(VERDICTS).nullable().optional(),
    stability: z.enum(["STABLE", "BORDERLINE"]).nullable().optional(),
    score: z.number().nullable().optional(),
    confidence: z.number().nullable().optional(),
    reason: z.string().nullable().optional(),
    gap_evidence_ids: z.array(z.string()).default([]),
    partial: z.boolean().nullable().optional(),
    needs_input: z.array(z.string()).default([]),
  })
  .passthrough()
  .superRefine((idea, ctx) => {
    const fail = (message: string) => ctx.addIssue({ code: "custom", message });
    if (idea.verdict == null) {
      // no verdict: none of the M11-derived fields may be present
      if (idea.score != null || idea.confidence != null || idea.stability != null) fail("a score without a verdict");
      return;
    }
    if (idea.partial === true) fail("a verdict together with partial: true");
    if ((idea.verdict === "GO" || idea.verdict === "PIVOT") && (idea.stability == null || idea.score == null)) {
      fail(`${idea.verdict} without stability or score`);
    }
  });
export type DiscoverIdea = z.infer<typeof Idea>;

const Dropped = z
  .object({
    title: z.string(),
    idea_id: z.string().nullable().optional(),
    round: z.number().int().nullable().optional(),
    reason: z.string().nullable().optional(),
    defeated_by_evidence_ids: z.array(z.string()).default([]),
  })
  .passthrough();
export type DroppedIdea = z.infer<typeof Dropped>;

const DiscoverResult = z
  .object({
    ideas: z.array(Idea),
    dropped: z.array(Dropped).default([]),
    partial: z.boolean().nullable().optional(),
    degraded: z.boolean().nullable().optional(),
    notes: z.array(z.string()).default([]),
  })
  .passthrough();
export type DiscoverResult = z.infer<typeof DiscoverResult>;

export function parseDiscoverResult(raw: unknown): DiscoverResult | null {
  const parsed = DiscoverResult.safeParse(raw);
  return parsed.success ? parsed.data : null;
}

export const DROP_REASON: Record<string, string> = {
  fatal_objection: "A fatal objection was upheld",
  multiple_objections: "Several objections were upheld",
  no_gap_evidence: "No evidence of a market gap",
};
