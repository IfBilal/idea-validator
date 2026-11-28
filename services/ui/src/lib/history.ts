import { z } from "zod";
import { apiFetch } from "./api";
import { VERDICTS, parseCard, type VerdictLabel } from "./verdict";

/**
 * History, versions and compare (docs/harness/05; gateway models/ideas.py). Every response is parsed with
 * zod and a malformed one is an error, never a guess. Verdicts, scores and confidence are shown exactly as
 * the gateway returned them: this file never recomputes, rounds or ranks anything.
 */
const Verdict = z.enum(VERDICTS).nullable();
const Stamp = z.iso.datetime({ offset: true });

const IdeaSummary = z.object({
  idea_id: z.string().min(1),
  title: z.string(),
  source: z.string(),
  latest_version: z.number().int().min(1),
  latest_verdict: Verdict,
  score: z.number().nullable(),
  updated_at: Stamp,
});
const IdeaPage = z.object({ items: z.array(IdeaSummary), next_cursor: z.string().nullable() });
export type IdeaSummary = z.infer<typeof IdeaSummary>;

const VersionSummary = z.object({
  version: z.number().int().min(1),
  text: z.string(),
  change_note: z.string().nullable(),
  parent_version: z.number().int().nullable(),
  created_at: Stamp,
  verdict: Verdict,
  score: z.number().nullable(),
  stability: z.string().nullable(),
});
export type VersionSummary = z.infer<typeof VersionSummary>;

/** A stored card must agree with its row (the gateway checks the same); if not, nothing is shown. */
const VersionCard = VersionSummary.extend({
  idea_id: z.string().min(1),
  title: z.string(),
  card: z.record(z.string(), z.unknown()).nullable(),
}).refine((v) => v.card === null || !("verdict" in v.card) || v.card.verdict === v.verdict, {
  message: "the stored card disagrees with its verdict",
});
export type VersionCard = z.infer<typeof VersionCard>;

const Scores = z.record(z.string(), z.number().nullable());
const CompareRow = z.object({
  idea_id: z.string().min(1),
  title: z.string(),
  version: z.number().int().min(1),
  verdict: Verdict,
  stability: z.string().nullable(),
  score: z.number().nullable(),
  confidence: z.number().nullable(),
  scores: Scores,
});
const Comparison = z.object({
  ideas: z.array(CompareRow).min(2),
  dimensions: z.array(z.object({ dimension: z.string(), scores: Scores, delta: z.number().nullable() })),
  verdicts: z.record(z.string(), Verdict),
});

const ideaPath = (id: string) => `/ideas/${encodeURIComponent(id)}`;

export const listIdeas = (cursor: string | null, limit = 20) =>
  apiFetch(`/ideas?limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, IdeaPage);
export const listVersions = (id: string) => apiFetch(`${ideaPath(id)}/versions`, z.array(VersionSummary));
export const getVersionCard = (id: string, version: number) => apiFetch(`${ideaPath(id)}/versions/${version}`, VersionCard);
export const getComparison = (ids: string[]) =>
  apiFetch(`/compare?ids=${ids.map(encodeURIComponent).join(",")}`, Comparison);

export type GateMap = Record<string, string>;
/** One column of the comparison table. `gates` is null when the stored card does not say (or could not be read). */
export interface Side {
  title: string;
  version: number;
  verdict: VerdictLabel | null;
  stability: string | null;
  score: number | null;
  confidence: number | null;
  scores: Record<string, number | null>;
  gates: GateMap | null;
}

const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
export interface Ref {
  id: string;
  version: number | null;
}
/** "<idea id>" (latest version) or "<idea id>:<version>". */
export function parseRef(raw: string | undefined): Ref | null {
  const m = new RegExp(`^(${UUID})(?::([1-9][0-9]{0,5}))?$`).exec(raw ?? "");
  return m ? { id: m[1]!, version: m[2] ? Number(m[2]) : null } : null;
}

function sideOfCard(v: VersionCard): Side {
  const base = { title: v.title, version: v.version, verdict: v.verdict, stability: v.stability, score: v.score };
  if (v.card === null) return { ...base, confidence: null, scores: {}, gates: null };
  const parsed = parseCard(v.card);
  if (parsed.kind === "invalid") throw new Error("invalid stored card"); // fail closed
  if (parsed.kind === "partial") return { ...base, confidence: null, scores: {}, gates: null };
  const { confidence, scores, gates } = parsed.card;
  return { ...base, confidence, scores: Object.fromEntries(scores.map((s) => [s.dimension, s.score])), gates };
}

/** Two ideas (latest versions) come from /compare; any explicit version is compared from its stored card. */
export async function loadComparison(a: Ref, b: Ref): Promise<[Side, Side]> {
  if (a.version === null && b.version === null) {
    const { ideas } = await getComparison([a.id, b.id]);
    const [ra, rb] = [ideas.find((i) => i.idea_id === a.id), ideas.find((i) => i.idea_id === b.id)];
    if (!ra || !rb) throw new Error("the comparison does not match the request");
    const withGates = async (r: typeof ra): Promise<Side> => {
      try {
        return { ...r, gates: sideOfCard(await getVersionCard(r.idea_id, r.version)).gates };
      } catch {
        return { ...r, gates: null }; // the table says "unavailable"; the verdict and scores still come from /compare
      }
    };
    return [await withGates(ra), await withGates(rb)];
  }
  const latest = async (r: Ref) => {
    if (r.version !== null) return r.version;
    const all = (await listVersions(r.id)).map((v) => v.version);
    if (all.length === 0) throw new Error("the idea has no versions");
    return Math.max(...all);
  };
  const load = async (r: Ref) => sideOfCard(await getVersionCard(r.id, await latest(r)));
  return [await load(a), await load(b)];
}
