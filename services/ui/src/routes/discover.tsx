import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useRef, useState, type FormEvent } from "react";
import { RunPanel } from "@/components/RunPanel";
import { Button } from "@/components/ui/button";
import { Alert, Field, Input } from "@/components/ui/form";
import { DROP_REASON, parseDiscoverResult, type DiscoverIdea, type DiscoverResult } from "@/lib/discover";
import { getRunResult, startDiscover } from "@/lib/endpoints";
import { errorMessage } from "@/lib/errors";
import { isTerminal, type RunState } from "@/lib/runState";
import { useRun } from "@/lib/useRun";

const DEFAULT_COUNT = 5;

export function DiscoverPage() {
  const [niche, setNiche] = useState("");
  const [count, setCount] = useState(String(DEFAULT_COUNT));
  const [countError, setCountError] = useState<string>();
  const key = useRef<string>(crypto.randomUUID()); // one key per submitted form: a retry starts one run
  const start = useMutation({
    mutationFn: (n: number) => startDiscover({ niche: niche.trim() === "" ? null : niche.trim(), count: n, deep_search: false }, key.current),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const text = count.trim();
    const n = /^\d+$/.test(text) ? Number(text) : Number.NaN;
    if (!Number.isInteger(n) || n < 1 || n > 10) {
      setCountError("Enter a whole number from 1 to 10");
      return;
    }
    setCountError(undefined);
    start.mutate(n);
  }

  if (start.data) return <RunView runId={start.data.run_id} />;

  return (
    <section className="max-w-2xl space-y-4">
      <h1 className="text-2xl font-semibold">Discover ideas</h1>
      <p className="text-sm text-[var(--muted)]">
        We look for gaps in real evidence, propose ideas that fit your profile, and let a skeptic challenge each one.
      </p>
      <form onSubmit={submit} noValidate className="space-y-4">
        <Field label="Niche (optional)" hint="For example devtools or education. Leave empty to use your interests.">
          {({ id, describedBy }) => (
            <Input id={id} aria-describedby={describedBy} value={niche} maxLength={200} onChange={(e) => setNiche(e.target.value)} />
          )}
        </Field>
        <Field label="How many ideas (1 to 10)" error={countError}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              inputMode="numeric"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              value={count}
              onChange={(e) => setCount(e.target.value)}
              className="w-24"
            />
          )}
        </Field>
        {start.isError && <Alert>{errorMessage(start.error)}</Alert>}
        <Button type="submit" disabled={start.isPending}>
          {start.isPending ? "Starting…" : "Discover"}
        </Button>
      </form>
    </section>
  );
}

function RunView({ runId }: { runId: string }) {
  const { state, status } = useRun(runId);
  return (
    <section className="max-w-3xl space-y-4">
      <h1 className="text-2xl font-semibold">Discovering ideas</h1>
      <RunPanel state={state} status={status} />
      {isTerminal(state.phase) ? <FinalResult runId={runId} state={state} /> : <Streaming state={state} />}
    </section>
  );
}

function Streaming({ state }: { state: RunState }) {
  return (
    <div className="space-y-3">
      {state.partials.length > 0 && (
        <p role="status" className="text-sm text-[var(--muted)]">
          {state.partials.length} idea{state.partials.length === 1 ? "" : "s"} finished so far. The ranked list appears when the run ends.
        </p>
      )}
      <DroppedList items={state.dropped.map((d) => ({ title: d.title, round: d.round ?? null, defeated_by_evidence_ids: [] }))} />
    </div>
  );
}

function FinalResult({ runId, state }: { runId: string; state: RunState }) {
  const query = useQuery({ queryKey: ["run-result", runId], queryFn: () => getRunResult(runId) });
  if (query.isPending) return <p role="status">Loading your ideas…</p>;
  const streamedDropped = state.dropped.map((d) => ({ title: d.title, round: d.round ?? null, defeated_by_evidence_ids: [] as string[] }));
  if (query.isError) return <Alert>{errorMessage(query.error)}</Alert>;
  if (query.data === null || query.data === undefined) {
    return (
      <div className="space-y-3">
        <Alert>No result was produced for this run.</Alert>
        <DroppedList items={streamedDropped} />
      </div>
    );
  }
  const result = parseDiscoverResult(query.data);
  if (result === null) return <Alert>The result could not be displayed. Please run the discovery again.</Alert>;
  return <ResultView result={result} stopped={state.phase !== "done"} />;
}

function ResultView({ result, stopped }: { result: DiscoverResult; stopped: boolean }) {
  const unvalidated = result.ideas.some((i) => i.verdict == null);
  return (
    <div className="space-y-4">
      {(result.partial === true || stopped) && (
        <Alert tone="info">The results are partial: the run did not finish everything it planned.</Alert>
      )}
      {unvalidated && <Alert tone="info">Some ideas are not fully validated yet and have no verdict.</Alert>}
      {result.ideas.length === 0 ? (
        <p>No ideas survived the checks this time. Try another niche or a different count.</p>
      ) : (
        <ol aria-label="Ranked ideas" className="space-y-3">
          {result.ideas.map((idea) => (
            <IdeaItem key={`${idea.idea_id}-${idea.version}`} idea={idea} />
          ))}
        </ol>
      )}
      <DroppedList items={result.dropped} />
    </div>
  );
}

function IdeaItem({ idea }: { idea: DiscoverIdea }) {
  const validated = idea.verdict != null;
  return (
    <li className="space-y-2 rounded-lg border border-[var(--border)] p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-lg font-semibold">{idea.title}</h3>
        {idea.niche && <span className="text-xs text-[var(--muted)]">{idea.niche}</span>}
      </div>
      {idea.summary && <p>{idea.summary}</p>}
      {validated ? (
        <p className="flex flex-wrap items-center gap-3 text-sm">
          <span className="rounded border-2 px-2 py-0.5 font-bold">{idea.verdict === "INSUFFICIENT_EVIDENCE" ? "NOT ENOUGH EVIDENCE" : idea.verdict}</span>
          {idea.stability && <span className="rounded border border-[var(--border)] px-2 py-0.5 text-xs">{idea.stability}</span>}
          {idea.score != null && (
            <span>
              Score <strong>{String(idea.score)}</strong> / 100
            </span>
          )}
          {idea.confidence != null && (
            <span>
              Confidence <strong>{String(idea.confidence)}</strong>
            </span>
          )}
        </p>
      ) : (
        <p className="text-sm text-[var(--muted)]">
          Not validated yet: no verdict.
          {idea.needs_input.length > 0 ? ` Needs: ${idea.needs_input.map((n) => n.replace(/_/g, " ")).join(", ")}.` : ""}
        </p>
      )}
      {idea.reason && <p className="text-sm text-[var(--muted)]">{idea.reason}</p>}
      <Link to="/ideas/$ideaId" params={{ ideaId: idea.idea_id }} search={{ v: idea.version }} className="text-sm underline" aria-label={`Open card: ${idea.title}`}>
        Open card
      </Link>
    </li>
  );
}

function DroppedList({ items }: { items: { title: string; round?: number | null; reason?: string | null; defeated_by_evidence_ids: string[] }[] }) {
  if (items.length === 0) return null;
  return (
    <details className="rounded-lg border border-[var(--border)] p-4" open>
      <summary className="cursor-pointer font-medium">Dropped by the skeptic ({items.length})</summary>
      <ul aria-label="Dropped ideas" className="mt-2 space-y-2 text-sm">
        {items.map((d, i) => (
          <li key={`${i}-${d.title}`}>
            <span className="font-medium">{d.title}</span>
            {d.round != null && <span className="text-[var(--muted)]"> (round {d.round})</span>}
            {d.reason && <span> — {DROP_REASON[d.reason] ?? d.reason.replace(/_/g, " ")}</span>}
            {d.defeated_by_evidence_ids.length > 0 && (
              <span className="text-[var(--muted)]"> · {d.defeated_by_evidence_ids.length} evidence item{d.defeated_by_evidence_ids.length === 1 ? "" : "s"}</span>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}
