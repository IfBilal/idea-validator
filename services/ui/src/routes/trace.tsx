import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { Alert } from "@/components/ui/form";
import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/errors";
import { getTrace, type Trace, type TraceNode } from "@/lib/trace";

const usd = (n: number) => `$${n}`;
const ms = (n: number) => `${n} ms`;

function traceError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) return "No trace found for this run.";
    if (error.status === 503) return "Traces are unavailable right now. Please try again later.";
    if (error.code === "BAD_RESPONSE") return "The server sent a trace this app does not understand, so nothing is shown.";
  }
  return errorMessage(error);
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt className="text-xs text-[var(--muted)]">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}

function Totals({ trace }: { trace: Trace }) {
  const t = trace.totals;
  return (
    <>
      <section aria-label="Run totals" className="rounded-md border border-[var(--border)] p-4">
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Steps" value={t.steps} />
          <Stat label="Tokens in" value={t.tokens_in} />
          <Stat label="Tokens out" value={t.tokens_out} />
          <Stat label="Cost" value={usd(t.cost_usd)} />
          <Stat label="Summed step time" value={ms(t.latency_ms)} />
          <Stat label="Wall time" value={ms(t.wall_ms)} />
          <Stat label="Errors" value={t.errors} />
        </dl>
      </section>
      <section aria-label="Totals by agent" className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="text-xs text-[var(--muted)]">
              {["Agent", "Steps", "Tokens in", "Tokens out", "Cost", "Time", "Errors"].map((h) => (
                <th key={h} scope="col" className="py-1 pr-4 font-normal">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(trace.by_agent).map(([agent, a]) => (
              <tr key={agent} className="border-t border-[var(--border)]">
                <th scope="row" className="py-1 pr-4 font-medium">
                  {agent}
                </th>
                <td className="pr-4">{a.steps}</td>
                <td className="pr-4">{a.tokens_in}</td>
                <td className="pr-4">{a.tokens_out}</td>
                <td className="pr-4">{usd(a.cost_usd)}</td>
                <td className="pr-4">{ms(a.latency_ms)}</td>
                <td>{a.errors}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function Node({ node, level }: { node: TraceNode; level: number }) {
  const [open, setOpen] = useState(true);
  const r = node.record;
  const name = r.tool ?? r.model ?? r.agent;
  const label = `${r.kind} ${name} by ${r.agent}`;
  const parent = node.children.length > 0;
  return (
    <li role="treeitem" aria-level={level} aria-expanded={parent ? open : undefined} aria-label={label}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-1 text-sm" style={{ paddingLeft: `${(level - 1) * 1.25}rem` }}>
        {parent ? (
          <button type="button" aria-expanded={open} aria-label={`${open ? "Collapse" : "Expand"} ${label}`} className="w-5 text-left" onClick={() => setOpen(!open)}>
            {open ? "▾" : "▸"}
          </button>
        ) : (
          <span className="w-5" aria-hidden="true" />
        )}
        <span className="rounded bg-[var(--border)] px-1.5 text-xs">{r.kind}</span>
        <span className="font-medium">{name}</span>
        <span className="text-[var(--muted)]">{r.agent}</span>
        <span>{ms(r.latency_ms)}</span>
        <span>
          {r.tokens_in} in / {r.tokens_out} out
        </span>
        <span>{usd(r.cost_usd)}</span>
        <span className={r.error === null ? "" : "text-red-600 dark:text-red-400"}>{r.error === null ? "ok" : `error: ${r.error}`}</span>
        {node.orphan && <span className="text-xs text-[var(--muted)]">parent step not recorded</span>}
      </div>
      {parent && open && (
        <ul role="group">
          {node.children.map((c) => (
            <Node key={c.record_id} node={c} level={level + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

export function TracePage({ runId }: { runId: string }) {
  const query = useQuery({ queryKey: ["trace", runId], queryFn: () => getTrace(runId) });
  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-semibold">Run trace</h1>
      <p className="text-xs text-[var(--muted)]">Run {runId}</p>
      {query.isPending && <p role="status">Loading the trace…</p>}
      {query.isError && <Alert>{traceError(query.error)}</Alert>}
      {query.data && (
        <>
          {query.data.truncated && <p role="status">This trace was cut short; later steps are not shown.</p>}
          <Totals trace={query.data} />
          {query.data.tree.length === 0 ? (
            <p>No steps were recorded for this run.</p>
          ) : (
            <ul role="tree" aria-label="Trace steps">
              {query.data.tree.map((n) => (
                <Node key={n.record_id} node={n} level={1} />
              ))}
            </ul>
          )}
        </>
      )}
      <Link to="/validate" className="text-sm underline">
        Back to Validate
      </Link>
    </section>
  );
}
