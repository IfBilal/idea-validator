import { useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { ApprovalDialog, QuestionCard } from "@/components/Prompts";
import { RunPanel } from "@/components/RunPanel";
import { Button } from "@/components/ui/button";
import { Alert, Field } from "@/components/ui/form";
import { VerdictCard } from "@/components/VerdictCard";
import { parseChallengeResult, parseWhatIfResult } from "@/lib/actions";
import { getRunResult, startChallenge, startWhatIf } from "@/lib/endpoints";
import { errorMessage } from "@/lib/errors";
import { isTerminal } from "@/lib/runState";
import { useRun } from "@/lib/useRun";
import { DIMENSION_LABEL, parseCard, type Claim, type Dimension, type SuggestedChange } from "@/lib/verdict";

const MAX_CUSTOM = 1000;

type Active = { kind: "challenge"; runId: string; claimText: string } | { kind: "whatif"; runId: string; label: string };

/**
 * A verdict card with its follow-up actions (U7, U8): challenge a claim, try a suggested change or a
 * custom one. Each action is a run of its own; its outcome is shown below the card. A new version is
 * rendered with its own ResultActions, so actions can be chained. One action at a time.
 */
export function ResultActions({ ideaId, version, raw }: { ideaId: string; version: number; raw: unknown }) {
  const [active, setActive] = useState<Active | null>(null);
  const [custom, setCustom] = useState("");
  const [customError, setCustomError] = useState<string>();
  const key = useRef<string>(crypto.randomUUID());
  const settled = parseCard(raw);
  const canWhatIf = settled.kind === "settled" && settled.card.verdict !== "GO" && settled.card.verdict !== "INSUFFICIENT_EVIDENCE";

  const challenge = useMutation({
    mutationFn: (claim: Claim) => startChallenge(ideaId, { version, claim_id: claim.claim_id ?? "" }, key.current),
    onSuccess: (r, claim) => setActive({ kind: "challenge", runId: r.run_id, claimText: claim.text }),
  });
  const whatIf = useMutation({
    mutationFn: (v: { change_id: string } | { custom_change: string }) => startWhatIf(ideaId, { version, ...v }, key.current),
    onSuccess: (r, v) =>
      setActive({ kind: "whatif", runId: r.run_id, label: "change_id" in v ? "the suggested change" : "your change" }),
  });

  const busy = challenge.isPending || whatIf.isPending || active !== null;

  function beginAction() {
    key.current = crypto.randomUUID(); // a new action is a new idempotency scope
  }

  function submitCustom(event: FormEvent) {
    event.preventDefault();
    const text = custom.trim();
    if (text === "") return setCustomError("Describe the change you want to try");
    if (text.length > MAX_CUSTOM) return setCustomError(`At most ${MAX_CUSTOM} characters`);
    setCustomError(undefined);
    beginAction();
    whatIf.mutate({ custom_change: text });
  }

  return (
    <div className="space-y-4">
      <VerdictCard
        raw={raw}
        onChallenge={busy ? undefined : (claim) => (beginAction(), challenge.mutate(claim))}
        onApplyChange={busy ? undefined : (change: SuggestedChange) => (beginAction(), whatIf.mutate({ change_id: change.change_id }))}
      />
      {challenge.isError && <Alert>{errorMessage(challenge.error)}</Alert>}
      {whatIf.isError && <Alert>{errorMessage(whatIf.error)}</Alert>}

      {canWhatIf && !active && (
        <form onSubmit={submitCustom} noValidate aria-label="Try your own change" className="space-y-2">
          <Field label="Try your own change" hint="Describe one change to the idea; we re-run the verdict on it as a new version." error={customError}>
            {({ id, describedBy, invalid }) => (
              <textarea
                id={id}
                rows={3}
                aria-describedby={describedBy}
                aria-invalid={invalid}
                value={custom}
                onChange={(e) => setCustom(e.target.value)}
                className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-sm aria-[invalid=true]:border-red-500"
              />
            )}
          </Field>
          <Button type="submit" variant="outline" disabled={busy}>
            {whatIf.isPending ? "Starting…" : "Try it"}
          </Button>
        </form>
      )}

      {active && <ActionRun key={active.runId} ideaId={ideaId} version={version} active={active} onClose={() => setActive(null)} />}
    </div>
  );
}

function ActionRun({ ideaId, version, active, onClose }: { ideaId: string; version: number; active: Active; onClose: () => void }) {
  const { state, status } = useRun(active.runId);
  const done = isTerminal(state.phase);
  const result = useQuery({ queryKey: ["action-result", active.runId], queryFn: () => getRunResult(active.runId), enabled: done });
  const title = active.kind === "challenge" ? `Challenging: “${active.claimText}”` : `Trying ${active.label}`;

  return (
    <section aria-label={active.kind === "challenge" ? "Challenge" : "What-if"} className="space-y-3 rounded-lg border border-[var(--border)] p-4">
      <h3 className="font-medium">{title}</h3>
      <RunPanel state={state} status={status} />
      {state.phase === "waiting_answer" && state.question && <QuestionCard key={state.question.questionId} runId={active.runId} question={state.question} />}
      {state.phase === "waiting_approval" && state.approval && <ApprovalDialog key={state.approval.approvalId} runId={active.runId} approval={state.approval} />}
      {done && result.isPending && <p role="status">Loading the outcome…</p>}
      {done && result.isError && <Alert>{errorMessage(result.error)}</Alert>}
      {done && result.isSuccess && (
        <Outcome kind={active.kind} ideaId={ideaId} version={version} raw={result.data} stopReason={state.reason} />
      )}
      {done && (
        <Button variant="outline" onClick={onClose}>
          Close
        </Button>
      )}
    </section>
  );
}

function Outcome({ kind, ideaId, version, raw, stopReason }: { kind: "challenge" | "whatif"; ideaId: string; version: number; raw: unknown; stopReason?: string }) {
  if (kind === "challenge") {
    const out = parseChallengeResult(raw);
    if (out.kind === "invalid") return <Alert>The outcome could not be displayed. Please try again.</Alert>;
    if (out.kind === "stopped") {
      return <p role="status">The check did not finish{out.reason ? `: ${out.reason}` : stopReason ? `: ${stopReason}` : ""}. Nothing was changed.</p>;
    }
    if (out.kind === "defended") {
      return (
        <div role="status" className="space-y-1">
          <p className="font-medium">Defended</p>
          {out.quote && <blockquote className="border-l-2 border-[var(--border)] pl-3 text-sm">{out.quote}</blockquote>}
          {out.reason && <p className="text-sm text-[var(--muted)]">{out.reason}</p>}
          <p className="text-sm">The claim stays and the verdict is unchanged.</p>
        </div>
      );
    }
    return (
      <div className="space-y-3">
        <div role="status" className="space-y-1">
          <p className="font-medium">Removed: the evidence did not support this claim</p>
          {out.reason && <p className="text-sm text-[var(--muted)]">{out.reason}</p>}
          {out.newVersion > 0 && <p className="text-sm">The verdict was recomputed without it: new version v{out.newVersion}.</p>}
        </div>
        {out.newVersion > 0 && out.card.kind !== "invalid" && <ResultActions ideaId={ideaId} version={out.newVersion} raw={rawCard(raw)} />}
      </div>
    );
  }
  const out = parseWhatIfResult(raw);
  if (out.card.kind === "invalid") return <Alert>The outcome could not be displayed. Please try again.</Alert>;
  if (out.card.kind === "partial") return <VerdictCard raw={raw} />;
  const next = out.version ?? version + 1;
  return (
    <div className="space-y-3">
      <div role="status" className="space-y-1">
        <p className="font-medium">
          New version v{next}
          {out.baseVersion !== undefined ? ` (from v${out.baseVersion})` : ""}
        </p>
        {out.changeNote && <p className="text-sm text-[var(--muted)]">{out.changeNote}</p>}
        {out.diff && <DiffView diff={out.diff} />}
      </div>
      <ResultActions ideaId={ideaId} version={next} raw={raw} />
    </div>
  );
}

function rawCard(result: unknown): unknown {
  return typeof result === "object" && result !== null ? (result as { card?: unknown }).card : undefined;
}

function DiffView({ diff }: { diff: NonNullable<ReturnType<typeof parseWhatIfResult>["diff"]> }) {
  return (
    <div className="space-y-1 text-sm">
      <p>
        Verdict: {diff.verdict_from ?? "—"} → {diff.verdict_to ?? "—"}
        {diff.verdict_changed ? " (changed)" : " (unchanged)"}
        {diff.score_from != null && diff.score_to != null ? ` · score ${String(diff.score_from)} → ${String(diff.score_to)}` : ""}
      </p>
      {diff.dimensions.length > 0 && (
        <ul aria-label="Dimension changes" className="list-disc pl-5 text-[var(--muted)]">
          {diff.dimensions
            .filter((d) => d.delta !== 0)
            .map((d) => (
              <li key={d.dimension}>
                {DIMENSION_LABEL[d.dimension as Dimension] ?? d.dimension}: {String(d.from)} → {String(d.to)} ({d.delta > 0 ? "+" : ""}
                {String(d.delta)})
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}
