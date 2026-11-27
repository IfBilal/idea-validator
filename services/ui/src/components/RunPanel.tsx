import type { RunPhase, RunState, Spend } from "@/lib/runState";
import type { StreamStatus } from "@/lib/sse";

const PHASE_LABEL: Record<RunPhase, string> = {
  starting: "Starting",
  running: "Running",
  waiting_answer: "Waiting for your answer",
  waiting_approval: "Waiting for your approval",
  done: "Done",
  failed: "Failed",
  stopped: "Stopped (budget reached)",
};

const money = (n: number | undefined) => (n === undefined ? "—" : `$${n.toFixed(3)}`);

function SpendMeter({ spent, budget }: { spent?: Partial<Spend>; budget?: Partial<Spend> }) {
  const used = spent?.cost_usd;
  const cap = budget?.cost_usd;
  const ratio = used !== undefined && cap ? used / cap : 0;
  const warn = ratio >= 0.8;
  return (
    <div aria-label="Spend" className="space-y-1">
      <p className="text-sm">
        <span className={warn ? "font-semibold text-amber-600 dark:text-amber-400" : ""}>
          {money(used)} / {money(cap)}
        </span>
        <span className="text-[var(--muted)]">
          {" "}
          · {spent?.tokens ?? 0} tokens · {spent?.pages ?? 0} pages
        </span>
      </p>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.min(100, Math.round(ratio * 100))}
        aria-label="Budget used"
        className="h-2 overflow-hidden rounded bg-[var(--border)]"
      >
        <div
          className={warn ? "h-full bg-amber-500" : "h-full bg-[var(--accent)]"}
          style={{ width: `${Math.min(100, ratio * 100)}%` }}
        />
      </div>
    </div>
  );
}

export function RunPanel({ state, status }: { state: RunState; status: StreamStatus }) {
  const reconnecting = status === "reconnecting";
  return (
    <section aria-label="Live run" className="space-y-4 rounded-lg border border-[var(--border)] p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">{PHASE_LABEL[state.phase]}</h2>
        <span className="text-xs text-[var(--muted)]">
          {state.kind ? `${state.kind} run` : ""}
          {reconnecting ? " · reconnecting…" : ""}
        </span>
      </header>
      {state.currentAgent && (
        <p>
          Now: <strong>{state.currentAgent}</strong>
          {state.round ? ` (round ${state.round})` : ""}
        </p>
      )}
      <SpendMeter spent={state.spent} budget={state.budget} />
      {state.steps.length > 0 && (
        <ol aria-label="Steps" className="space-y-1 text-sm">
          {state.steps.map((s) => (
            <li key={s.stepId} className="flex gap-2">
              <span aria-hidden>{s.status === "done" ? "✔" : "…"}</span>
              <span>
                {s.agent}
                <span className="sr-only">{s.status === "done" ? " finished" : " in progress"}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
      {state.toolCalls.length > 0 && (
        <div>
          <h3 className="text-sm font-medium">Tool calls</h3>
          <ul aria-label="Tool calls" className="max-h-48 space-y-1 overflow-auto text-xs">
            {state.toolCalls.slice(-20).map((c) => (
              <li key={c.seq} className="flex flex-wrap gap-2">
                <code>{c.tool}</code>
                <span className="text-[var(--muted)]">{c.summary}</span>
                {c.latencyMs !== undefined && <span>{c.latencyMs} ms</span>}
                {c.cacheHit && <span className="rounded border border-[var(--border)] px-1">cache</span>}
                {c.error && <span className="rounded border border-red-500 px-1 text-red-600">error</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {(state.phase === "failed" || state.phase === "stopped") && (
        <p role="alert">
          {state.phase === "failed"
            ? "The run could not finish."
            : "The run stopped because it reached its budget."}
          {state.reason ? ` ${state.reason}` : ""}
        </p>
      )}
    </section>
  );
}
