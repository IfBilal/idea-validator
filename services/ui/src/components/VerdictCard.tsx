import { Button } from "@/components/ui/button";
import {
  DIMENSIONS,
  DIMENSION_LABEL,
  GATE_LABEL,
  MISSING_TEXT,
  parseCard,
  safeHref,
  type Claim,
  type PartialCard,
  type SettledCard,
} from "@/lib/verdict";

const BADGE: Record<string, string> = {
  GO: "border-green-600 text-green-700 dark:text-green-300",
  PIVOT: "border-amber-500 text-amber-700 dark:text-amber-300",
  "NO-GO": "border-red-600 text-red-700 dark:text-red-300",
  INSUFFICIENT_EVIDENCE: "border-[var(--border)] text-[var(--muted)]",
};

/** Round half up in decimal terms (0.825 -> 0.83); toFixed would show 0.82 because of binary floats. */
const fmt = (n: number, d = 1) => {
  const k = 10 ** d;
  return (Math.round(Number((n * k).toPrecision(12))) / k).toFixed(d).replace(/\.0+$/, "");
};

function EvidenceLinks({ ids, card }: { ids: string[]; card: SettledCard }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      {ids.map((id) => {
        const item = card.evidence[id];
        const href = safeHref(item?.url);
        const quote = item?.quote ?? item?.text;
        const label = id.replace(/^card_/, "");
        return href ? (
          <a key={id} href={href} target="_blank" rel="noopener noreferrer" title={quote} className="rounded border border-[var(--border)] px-1 text-xs underline">
            {label}↗
          </a>
        ) : (
          <span key={id} title={quote} className="rounded border border-[var(--border)] px-1 text-xs">
            {label}
          </span>
        );
      })}
    </span>
  );
}

function PartialView({ card }: { card: PartialCard }) {
  // Rendering rule (docs/harness/05): a card without a verdict never shows GO / PIVOT / NO-GO or a score.
  const needsInput = card.needs_input.length > 0;
  return (
    <section aria-label="Result" className="space-y-3 rounded-lg border border-[var(--border)] p-4">
      <h2 className="text-lg font-semibold">{needsInput ? "Needs your input" : "No verdict this time"}</h2>
      {needsInput ? (
        <>
          <p>We will not guess. Please provide:</p>
          <ul className="list-disc pl-5">
            {card.needs_input.map((n) => (
              <li key={n}>{MISSING_TEXT(n)}</li>
            ))}
          </ul>
        </>
      ) : (
        <p>
          The run stopped before a verdict could be given.
          {card.stop_reason ? ` Reason: ${card.stop_reason}.` : ""}
          {card.error ? " Something went wrong while checking the result." : ""}
        </p>
      )}
      {card.missing.length > 0 && (
        <ul aria-label="What was missing" className="list-disc pl-5 text-sm text-[var(--muted)]">
          {card.missing.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ClaimRow({ claim, card, onChallenge }: { claim: Claim; card: SettledCard; onChallenge?: (claim: Claim) => void }) {
  return (
    <li className="space-y-1">
      <p className={claim.removed ? "text-[var(--muted)] line-through" : ""}>{claim.text}</p>
      <div className="flex flex-wrap items-center gap-2">
        <EvidenceLinks ids={claim.evidence_ids} card={card} />
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

export function VerdictCard({ raw, onChallenge }: { raw: unknown; onChallenge?: (claim: Claim) => void }) {
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
  const failedGates = Object.entries(card.gates).filter(([, v]) => v !== "pass");
  return (
    <section aria-label="Verdict" className="space-y-5 rounded-lg border border-[var(--border)] p-4">
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-3">
          <span className={`rounded border-2 px-3 py-1 text-lg font-bold ${BADGE[card.verdict] ?? ""}`}>
            {insufficient ? "NOT ENOUGH EVIDENCE" : card.verdict}
          </span>
          {card.stability && !insufficient && (
            <span className="rounded border border-[var(--border)] px-2 py-0.5 text-xs">{card.stability}</span>
          )}
          {!insufficient && (
            <span>
              Score <strong>{fmt(card.score)}</strong> / 100
            </span>
          )}
          <span>
            Confidence <strong>{fmt(card.confidence, 2)}</strong>
          </span>
          {card.version !== undefined && <span className="text-sm text-[var(--muted)]">v{card.version}</span>}
          {card.cache_hit && <span className="text-xs text-[var(--muted)]">cached</span>}
        </div>
        <p className="text-sm text-[var(--muted)]">Decision support, not a prediction of success.</p>
      </header>

      {insufficient ? (
        <div className="space-y-2">
          <p className="font-medium">We don&apos;t guess when the evidence is thin.</p>
          {card.missing.length > 0 && (
            <ul aria-label="What was missing" className="list-disc pl-5">
              {card.missing.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          )}
          <p className="text-sm text-[var(--muted)]">You can approve a deeper search or add more details to the idea.</p>
        </div>
      ) : (
        <>
          {card.verdict === "NO-GO" && failedGates.length > 0 && (
            <p role="note" className="rounded border border-red-600 p-2">
              Blocked by the {failedGates.map(([g]) => (GATE_LABEL[g] ?? g).toLowerCase()).join(" and ")} gate.
            </p>
          )}
          {card.reasons.length > 0 && (
            <ul aria-label="Why" className="list-disc pl-5 text-sm">
              {card.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
        </>
      )}

      {Object.keys(card.gates).length > 0 && (
        <div>
          <h3 className="mb-1 font-medium">Gates</h3>
          <ul className="flex flex-wrap gap-3 text-sm">
            {Object.entries(card.gates).map(([gate, result]) => (
              <li key={gate}>
                <span aria-hidden>{result === "pass" ? "✔" : "✖"}</span> {GATE_LABEL[gate] ?? gate}
                <span className="sr-only">{result === "pass" ? " passed" : " failed"}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h3 className="mb-2 font-medium">Six dimensions</h3>
        <ul className="space-y-3">
          {DIMENSIONS.map((d) => {
            const score = card.adjusted_scores[d];
            const claims = card.claims.filter((c) => c.dimension === d);
            return (
              <li key={d} className="space-y-1" aria-label={DIMENSION_LABEL[d]}>
                <div className="flex flex-wrap items-center gap-3">
                  <span className="w-40 font-medium">{DIMENSION_LABEL[d]}</span>
                  {score === null || score === undefined ? (
                    <span className="text-[var(--muted)]">no score (abstained)</span>
                  ) : (
                    <>
                      <span>{fmt(score)} / 5</span>
                      <div role="img" aria-label={`${fmt(score)} out of 5`} className="h-2 w-28 overflow-hidden rounded bg-[var(--border)]">
                        <div className="h-full bg-[var(--accent)]" style={{ width: `${(score / 5) * 100}%` }} />
                      </div>
                    </>
                  )}
                  {card.weights[d] !== undefined && <span className="text-xs text-[var(--muted)]">weight {fmt(card.weights[d])}</span>}
                </div>
                {claims.length > 0 && (
                  <ul className="space-y-1 pl-4 text-sm">
                    {claims.map((c, i) => (
                      <ClaimRow key={c.claim_id ?? i} claim={c} card={card} onChallenge={onChallenge} />
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      </div>

      {card.sensitivity && (
        <div>
          <h3 className="mb-1 font-medium">Stability</h3>
          <p className="text-sm">
            {card.sensitivity.flipped} of {card.sensitivity.runs} checks change the verdict.
          </p>
          {card.sensitivity.flips.length > 0 && (
            <ul className="list-disc pl-5 text-sm text-[var(--muted)]">
              {card.sensitivity.flips.map((f) => (
                <li key={f.change}>
                  {f.change} → {f.verdict} ({fmt(f.score)})
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {card.what_would_make_go.length > 0 && card.verdict !== "GO" && (
        <div>
          <h3 className="mb-1 font-medium">What would make this GO?</h3>
          <ul className="list-disc pl-5 text-sm">
            {card.what_would_make_go.map((w) => (
              <li key={`${w.dimension}-${w.to}`}>
                {DIMENSION_LABEL[w.dimension as keyof typeof DIMENSION_LABEL] ?? w.dimension}
                {w.from !== undefined && w.to !== undefined ? ` ${fmt(w.from)} → ${fmt(w.to)}` : ""}
                {w.new_score !== undefined ? ` gives ${fmt(w.new_score)}` : ""}
                {w.new_verdict ? ` (${w.new_verdict}${w.stable ? ", stable" : ""})` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}

      {card.notes.length > 0 && (
        <ul aria-label="Notes" className="list-disc pl-5 text-xs text-[var(--muted)]">
          {card.notes.map((n) => (
            <li key={n}>{n}</li>
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
