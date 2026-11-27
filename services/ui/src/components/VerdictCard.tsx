import { Button } from "@/components/ui/button";
import {
  DIMENSIONS,
  DIMENSION_LABEL,
  GATES,
  GATE_LABEL,
  MISSING_TEXT,
  parseCard,
  safeHref,
  type Claim,
  type Dimension,
  type PartialCard,
  type SettledCard,
  type SuggestedChange,
} from "@/lib/verdict";

/**
 * Renders a verdict card exactly as received. Numbers are shown unrounded (M11 already rounds),
 * gates are shown verbatim in their three states, and the explanation comes from M11's `reasons`:
 * the UI never decides which gate blocked, what the thresholds are, or what the verdict is.
 */
const BADGE: Record<string, string> = {
  GO: "border-green-600 text-green-700 dark:text-green-300",
  PIVOT: "border-amber-500 text-amber-700 dark:text-amber-300",
  "NO-GO": "border-red-600 text-red-700 dark:text-red-300",
  INSUFFICIENT_EVIDENCE: "border-[var(--border)] text-[var(--muted)]",
};

function EvidenceLinks({ ids, card }: { ids: string[]; card: SettledCard }) {
  return (
    <span className="inline-flex flex-wrap items-start gap-2">
      {ids.map((id, i) => {
        const item = card.evidence[id];
        const href = safeHref(item?.url);
        const quote = item?.quote ?? item?.text;
        return (
          <span key={`${i}-${id}`} className="inline-flex flex-wrap items-center gap-1">
            {href ? (
              <a href={href} target="_blank" rel="noopener noreferrer" className="rounded border border-[var(--border)] px-1 font-mono text-xs underline">
                {id}↗
              </a>
            ) : (
              <span className="rounded border border-[var(--border)] px-1 font-mono text-xs">{id}</span>
            )}
            {quote && (
              <details className="text-xs">
                <summary className="cursor-pointer">Quote</summary>
                <blockquote className="max-w-prose text-[var(--muted)]">{quote}</blockquote>
              </details>
            )}
          </span>
        );
      })}
    </span>
  );
}

function ClaimRow({ claim, card, onChallenge }: { claim: Claim; card: SettledCard; onChallenge?: (claim: Claim) => void }) {
  return (
    <li className="space-y-1">
      <p className={claim.removed ? "text-[var(--muted)] line-through" : ""}>{claim.text}</p>
      <div className="flex flex-wrap items-center gap-2">
        <EvidenceLinks ids={claim.evidence_ids} card={card} />
        {claim.evidence_ids.length === 0 && (
          <span role="note" className="text-xs text-amber-700 dark:text-amber-300">
            No evidence attached to this claim
          </span>
        )}
        {claim.supported === false && !claim.removed && <span className="text-xs">Not supported by its evidence</span>}
        {claim.removed && <span className="text-xs">Removed after a challenge</span>}
        {onChallenge && claim.claim_id && !claim.removed && (
          <Button variant="outline" className="px-2 py-0.5 text-xs" onClick={() => onChallenge(claim)}>
            Challenge
          </Button>
        )}
      </div>
    </li>
  );
}

function PartialView({ card }: { card: PartialCard }) {
  // Rendering rule (docs/harness/05): a card without a verdict never shows GO / PIVOT / NO-GO or a score.
  const needsInput = card.needs_input.length > 0;
  const missing = card.missing ?? [];
  return (
    <section aria-label="Result" className="space-y-3 rounded-lg border border-[var(--border)] p-4">
      <h2 className="text-lg font-semibold">{needsInput ? "Needs your input" : "No verdict this time"}</h2>
      {needsInput && (
        <>
          <p>We will not guess. Please provide:</p>
          <ul className="list-disc pl-5">
            {card.needs_input.map((n, i) => (
              <li key={`${i}-${n}`}>{MISSING_TEXT(n)}</li>
            ))}
          </ul>
        </>
      )}
      {(card.stop_reason || card.error || !needsInput) && (
        <p>
          {needsInput ? "The run also stopped early." : "The run stopped before a verdict could be given."}
          {card.stop_reason ? ` Reason: ${card.stop_reason}.` : ""}
          {card.error ? ` Problem: ${card.error}.` : ""}
        </p>
      )}
      {missing.length > 0 && (
        <ul aria-label="What was missing" className="list-disc pl-5 text-sm text-[var(--muted)]">
          {missing.map((m, i) => (
            <li key={`${i}-${m}`}>{m}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Gates({ card }: { card: SettledCard }) {
  return (
    <div>
      <h3 className="mb-1 font-medium">Gates</h3>
      <ul aria-label="Gates" className="flex flex-wrap gap-3 text-sm">
        {GATES.map((gate) => {
          const state = card.gates[gate];
          return (
            <li key={gate} aria-label={`${GATE_LABEL[gate]}: ${state.replace("_", " ")}`}>
              <span aria-hidden>{state === "pass" ? "✔" : state === "fail" ? "✖" : "–"}</span> {GATE_LABEL[gate]}
              <span className="text-[var(--muted)]"> ({state === "not_evaluated" ? "not evaluated" : state})</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Dimensions({ card, onChallenge }: { card: SettledCard; onChallenge?: (claim: Claim) => void }) {
  const byDimension = new Map(card.scores.map((s) => [s.dimension, s]));
  const known = new Set<string>(DIMENSIONS);
  const other = card.claims.filter((c) => !c.dimension || !known.has(c.dimension));
  return (
    <div>
      <h3 className="mb-2 font-medium">Six dimensions</h3>
      <ul className="space-y-3">
        {DIMENSIONS.map((d: Dimension) => {
          const s = byDimension.get(d);
          const claims = card.claims.filter((c) => c.dimension === d);
          const weight = card.weights[d];
          return (
            <li key={d} className="space-y-1" aria-label={DIMENSION_LABEL[d]}>
              <div className="flex flex-wrap items-center gap-3">
                <span className="w-40 font-medium">{DIMENSION_LABEL[d]}</span>
                {!s || s.score === null ? (
                  <span className="text-[var(--muted)]">no score (abstained)</span>
                ) : (
                  <>
                    <span>{String(s.score)} / 5</span>
                    <div role="img" aria-label={`${s.score} out of 5`} className="h-2 w-28 overflow-hidden rounded bg-[var(--border)]">
                      <div className="h-full bg-[var(--accent)]" style={{ width: `${(s.score / 5) * 100}%` }} />
                    </div>
                  </>
                )}
                {s?.confidence !== null && s?.confidence !== undefined && (
                  <span className="text-xs text-[var(--muted)]">confidence {String(s.confidence)}</span>
                )}
                {weight !== undefined && <span className="text-xs text-[var(--muted)]">weight {String(weight)}</span>}
                {s?.adjusted_by_challenge ? (
                  <span className="text-xs">({String(s.adjusted_by_challenge)} after challenge)</span>
                ) : null}
              </div>
              {claims.length > 0 && (
                <ul className="space-y-1 pl-4 text-sm">
                  {claims.map((c, i) => (
                    <ClaimRow key={`${d}-${i}-${c.claim_id ?? ""}`} claim={c} card={card} onChallenge={onChallenge} />
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      {other.length > 0 && (
        <div className="mt-3">
          <h4 className="text-sm font-medium">Other claims</h4>
          <ul className="space-y-1 pl-4 text-sm">
            {other.map((c, i) => (
              <ClaimRow key={`other-${i}-${c.claim_id ?? ""}`} claim={c} card={card} onChallenge={onChallenge} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function VerdictCard({
  raw,
  onChallenge,
  onApplyChange,
}: {
  raw: unknown;
  onChallenge?: (claim: Claim) => void;
  /** Called with a concrete suggested change (M8 text for one of M11's deltas); shown only when not GO. */
  onApplyChange?: (change: SuggestedChange) => void;
}) {
  const parsed = parseCard(raw);
  if (parsed.kind === "invalid") {
    return (
      <p role="alert" className="rounded-lg border border-red-500 p-4">
        The result could not be displayed. Please run the validation again.
      </p>
    );
  }
  if (parsed.kind === "partial") return <PartialView card={parsed.card} />;

  const card = parsed.card;
  const insufficient = card.verdict === "INSUFFICIENT_EVIDENCE";
  const reasons = card.reasons ?? [];
  const missing = card.missing ?? [];
  return (
    <section aria-label="Verdict" className="space-y-5 rounded-lg border border-[var(--border)] p-4">
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-3">
          <span className={`rounded border-2 px-3 py-1 text-lg font-bold ${BADGE[card.verdict] ?? ""}`}>
            {insufficient ? "NOT ENOUGH EVIDENCE" : card.verdict}
          </span>
          {!insufficient && card.stability && (
            <span className="rounded border border-[var(--border)] px-2 py-0.5 text-xs">{card.stability}</span>
          )}
          {!insufficient && card.score !== null && (
            <span>
              Score <strong>{String(card.score)}</strong> / 100
            </span>
          )}
          {card.confidence !== null && (
            <span>
              Confidence <strong>{String(card.confidence)}</strong>
            </span>
          )}
          {card.version !== undefined && <span className="text-sm text-[var(--muted)]">v{card.version}</span>}
          {card.cache_hit && <span className="text-xs text-[var(--muted)]">cached</span>}
        </div>
        <p className="text-sm text-[var(--muted)]">Decision support, not a prediction of success.</p>
      </header>

      {insufficient && <p className="font-medium">We don&apos;t guess when the evidence is thin.</p>}
      {reasons.length > 0 && (
        <ul aria-label="Why" className="list-disc pl-5 text-sm">
          {reasons.map((r, i) => (
            <li key={`${i}-${r}`}>{r}</li>
          ))}
        </ul>
      )}
      {missing.length > 0 && (
        <div>
          <h3 className="mb-1 font-medium">What was missing</h3>
          <ul aria-label="What was missing" className="list-disc pl-5">
            {missing.map((m, i) => (
              <li key={`${i}-${m}`}>{m}</li>
            ))}
          </ul>
        </div>
      )}
      {insufficient && <p className="text-sm text-[var(--muted)]">You can approve a deeper search or add more details to the idea.</p>}

      <Gates card={card} />

      {!insufficient && (
        <>
          <Dimensions card={card} onChallenge={onChallenge} />

          <div>
            <h3 className="mb-1 font-medium">How it was decided</h3>
            <p className="text-sm text-[var(--muted)]">
              Thresholds from the engine: GO at {String(card.thresholds.T_go)}, NO-GO below {String(card.thresholds.T_no)}, minimum
              dimension score {String(card.thresholds.floor)}.
              {card.weakest_dimension ? ` Weakest dimension: ${DIMENSION_LABEL[card.weakest_dimension as Dimension] ?? card.weakest_dimension}.` : ""}
            </p>
          </div>

          {card.sensitivity && (
            <div>
              <h3 className="mb-1 font-medium">Stability</h3>
              <p className="text-sm">
                {card.sensitivity.flipped} of {card.sensitivity.runs} checks change the verdict.
              </p>
              {card.sensitivity.note && <p className="text-xs text-[var(--muted)]">{card.sensitivity.note}</p>}
              {(card.sensitivity.flips ?? []).length > 0 && (
                <ul className="list-disc pl-5 text-sm text-[var(--muted)]">
                  {(card.sensitivity.flips ?? []).map((f, i) => (
                    <li key={`${i}-${String(f.change)}`}>
                      {String(f.change)} → {String(f.verdict)} ({String(f.score)})
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {card.verdict !== "GO" && card.changes.length > 0 && (
            <div>
              <h3 className="mb-1 font-medium">Suggested changes</h3>
              <ul className="space-y-2 text-sm">
                {card.changes.map((c, i) => (
                  <li key={`${i}-${c.change_id}`} className="space-y-1">
                    <p>{c.text}</p>
                    {c.rationale && <p className="text-xs text-[var(--muted)]">{c.rationale}</p>}
                    {onApplyChange && (
                      <Button variant="outline" className="px-2 py-0.5 text-xs" onClick={() => onApplyChange(c)}>
                        Try this change
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {card.verdict !== "GO" && card.what_would_make_go.length > 0 && (
            <div>
              <h3 className="mb-1 font-medium">What would make this GO?</h3>
              <ul className="space-y-1 text-sm">
                {card.what_would_make_go.map((w, i) => (
                  <li key={`${i}-${w.dimension ?? ""}-${w.to ?? ""}`}>
                    {w.change && <span>{w.change}. </span>}
                    {w.dimension && (
                      <span>
                        {DIMENSION_LABEL[w.dimension as Dimension] ?? w.dimension}
                        {w.from !== null && w.from !== undefined && w.to !== null && w.to !== undefined
                          ? ` ${String(w.from)} → ${String(w.to)}`
                          : ""}
                        {w.new_score !== null && w.new_score !== undefined ? ` gives ${String(w.new_score)}` : ""}
                      </span>
                    )}
                    <span>
                      {" "}
                      ({w.new_verdict}
                      {w.stable === true ? ", stable" : w.stable === false ? ", borderline" : ""})
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {card.notes.length > 0 && (
        <ul aria-label="Notes" className="list-disc pl-5 text-xs text-[var(--muted)]">
          {card.notes.map((n, i) => (
            <li key={`${i}-${n}`}>{n}</li>
          ))}
        </ul>
      )}
      {card.versions && (
        <p className="text-xs text-[var(--muted)]">
          Engine {card.versions.engine ?? "?"} · rubrics {card.versions.rubrics ?? "?"} · weights {card.versions.profile_weights ?? "?"}
        </p>
      )}
    </section>
  );
}
