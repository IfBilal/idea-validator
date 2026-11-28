import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Alert, Field, Select } from "@/components/ui/form";
import { errorMessage } from "@/lib/errors";
import { listIdeas, listVersions, loadComparison, parseRef, type Side } from "@/lib/history";
import { DIMENSIONS, DIMENSION_LABEL, GATES, GATE_LABEL } from "@/lib/verdict";
import { verdictText } from "@/routes/history";

type Cell = string | null; // null: the gateway has no value
const GATE_TEXT: Record<string, string> = { pass: "pass", fail: "fail", not_evaluated: "not evaluated" };

function Row({ label, a, b }: { label: string; a: Cell; b: Cell }) {
  const differs = a !== b;
  const show = (v: Cell, none: string) => (v === null ? <span className="text-[var(--muted)]">{none}</span> : v);
  const none = label === "Overall score" ? "No score" : label === "Verdict" ? "No verdict yet" : label.endsWith("gate") ? "Unavailable" : "Not scored";
  return (
    <tr className={differs ? "bg-[var(--border)]/30" : undefined}>
      <th scope="row" className="py-2 pr-4 text-left font-medium">{label}</th>
      <td className="py-2 pr-4">{show(a, none)}</td>
      <td className="py-2 pr-4">{show(b, none)}</td>
      <td className="py-2 font-semibold">{differs ? "differs" : ""}</td>
    </tr>
  );
}

const num = (v: number | null | undefined): Cell => (v === null || v === undefined ? null : String(v));

function CompareTable({ a, b }: { a: Side; b: Side }) {
  const extra = [...new Set([...Object.keys(a.scores), ...Object.keys(b.scores)])].filter((d) => !(DIMENSIONS as readonly string[]).includes(d));
  const dims = [...DIMENSIONS, ...extra.sort()];
  const verdict = (s: Side): Cell => (s.verdict === null ? null : `${verdictText(s.verdict)}${s.stability ? ` (${s.stability})` : ""}`);
  const gate = (s: Side, g: string): Cell => (s.gates === null ? null : (GATE_TEXT[s.gates[g] ?? ""] ?? s.gates[g] ?? null));
  return (
    <table aria-label="Comparison" className="w-full text-sm">
      <thead>
        <tr className="text-left">
          <th scope="col" className="pb-2">Measure</th>
          <th scope="col" className="pb-2">{a.title} (v{a.version})</th>
          <th scope="col" className="pb-2">{b.title} (v{b.version})</th>
          <th scope="col" className="pb-2">Difference</th>
        </tr>
      </thead>
      <tbody>
        <Row label="Verdict" a={verdict(a)} b={verdict(b)} />
        <Row label="Overall score" a={num(a.score)} b={num(b.score)} />
        <Row label="Confidence" a={num(a.confidence)} b={num(b.confidence)} />
        {dims.map((d) => (
          <Row key={d} label={(DIMENSION_LABEL as Record<string, string>)[d] ?? d.replace(/_/g, " ")} a={num(a.scores[d])} b={num(b.scores[d])} />
        ))}
        {GATES.map((g) => (
          <Row key={g} label={`${GATE_LABEL[g]} gate`} a={gate(a, g)} b={gate(b, g)} />
        ))}
      </tbody>
    </table>
  );
}

function Result({ a, b }: { a: string; b: string }) {
  const ra = parseRef(a);
  const rb = parseRef(b);
  const query = useQuery({
    queryKey: ["compare", a, b],
    queryFn: () => loadComparison(ra!, rb!),
    enabled: ra !== null && rb !== null,
  });
  if (ra === null || rb === null) return <Alert>This comparison link is not valid. Pick the two ideas again.</Alert>;
  if (query.isPending) return <p role="status">Loading the comparison…</p>;
  if (query.isError) return <Alert>{errorMessage(query.error)}</Alert>;
  return <CompareTable a={query.data[0]} b={query.data[1]} />;
}

/** Picks one idea and one version ("" = latest) and reports "<id>" or "<id>:<version>" ("" while unpicked). */
function SidePicker({ name, value, onChange }: { name: string; value: string; onChange: (v: string) => void }) {
  const [id = "", version = ""] = value.split(":");
  const ideas = useQuery({ queryKey: ["ideas-picker"], queryFn: () => listIdeas(null, 50) });
  const versions = useQuery({ queryKey: ["versions", id], queryFn: () => listVersions(id), enabled: id !== "" });
  const join = (i: string, v: string) => (i === "" ? "" : v === "" ? i : `${i}:${v}`);
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label={`${name} idea`} error={ideas.isError ? errorMessage(ideas.error) : undefined}>
        {({ id: fid, describedBy }) => (
          <Select id={fid} aria-describedby={describedBy} value={id} onChange={(e) => onChange(join(e.target.value, ""))}>
            <option value="">Choose an idea</option>
            {ideas.data?.items.map((i) => (
              <option key={i.idea_id} value={i.idea_id}>{i.title}</option>
            ))}
          </Select>
        )}
      </Field>
      <Field label={`${name} version`} error={versions.isError ? errorMessage(versions.error) : undefined}>
        {({ id: fid, describedBy }) => (
          <Select id={fid} aria-describedby={describedBy} value={version} disabled={id === ""} onChange={(e) => onChange(join(id, e.target.value))}>
            <option value="">Latest</option>
            {versions.data?.map((v) => (
              <option key={v.version} value={String(v.version)}>Version {v.version}</option>
            ))}
          </Select>
        )}
      </Field>
    </div>
  );
}

export function ComparePage({ a = "", b = "" }: { a?: string; b?: string }) {
  const navigate = useNavigate();
  const [left, setLeft] = useState(a);
  const [right, setRight] = useState(b);
  const [error, setError] = useState<string>();

  function submit(event: FormEvent) {
    event.preventDefault();
    if (left === "" || right === "") return setError("Choose both ideas to compare.");
    if (left === right) return setError("Choose two different ideas or two different versions.");
    setError(undefined);
    void navigate({ to: "/compare", search: { a: left, b: right } });
  }

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-semibold">Compare</h1>
      <form onSubmit={submit} noValidate className="max-w-3xl space-y-4">
        <SidePicker name="First" value={left} onChange={setLeft} />
        <SidePicker name="Second" value={right} onChange={setRight} />
        {error && <Alert>{error}</Alert>}
        <Button type="submit">Compare</Button>
      </form>
      {a !== "" && b !== "" && <Result a={a} b={b} />}
    </section>
  );
}
