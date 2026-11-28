import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/form";
import { listIdeas, listVersions, type VersionSummary } from "@/lib/history";
import { errorMessage } from "@/lib/errors";

export const verdictText = (v: string) => (v === "INSUFFICIENT_EVIDENCE" ? "NOT ENOUGH EVIDENCE" : v);

/** Verdict, score and stability exactly as stored; a missing verdict is said plainly. */
function Outcome({ verdict, score, stability }: { verdict: string | null; score: number | null; stability?: string | null }) {
  if (verdict === null) return <span className="text-[var(--muted)]">No verdict yet</span>;
  return (
    <span className="flex flex-wrap items-center gap-2">
      <span className="rounded border-2 px-2 py-0.5 font-bold">{verdictText(verdict)}</span>
      {stability && <span className="rounded border border-[var(--border)] px-2 py-0.5 text-xs">{stability}</span>}
      {score !== null && (
        <span>
          Score <strong>{String(score)}</strong> / 100
        </span>
      )}
    </span>
  );
}

const day = (stamp: string) => (
  <time dateTime={stamp} className="text-xs text-[var(--muted)]">
    {stamp.slice(0, 10)}
  </time>
);

export function HistoryPage() {
  const query = useInfiniteQuery({
    queryKey: ["ideas"],
    queryFn: ({ pageParam }) => listIdeas(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <section className="max-w-3xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">History</h1>
        <Link to="/compare" search={{}} className="text-sm underline">
          Compare two ideas
        </Link>
      </div>
      {query.isPending && <p role="status">Loading your ideas…</p>}
      {query.isError && <Alert>{errorMessage(query.error)}</Alert>}
      {query.isSuccess && items.length === 0 && (
        <p>
          No ideas yet. <Link to="/validate" className="underline">Validate your first idea</Link>.
        </p>
      )}
      {items.length > 0 && (
        <ul aria-label="Your ideas" className="space-y-3">
          {items.map((i) => (
            <li key={i.idea_id} className="space-y-2 rounded-lg border border-[var(--border)] p-4">
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="text-lg font-semibold">{i.title}</h2>
                <span className="text-xs text-[var(--muted)]">v{i.latest_version}</span>
                {day(i.updated_at)}
              </div>
              <Outcome verdict={i.latest_verdict} score={i.score} />
              <div>
                <Link to="/ideas/$ideaId" params={{ ideaId: i.idea_id }} search={{}} className="text-sm underline" aria-label={`Versions of ${i.title}`}>
                  Versions
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
      {query.hasNextPage && (
        <Button variant="outline" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
          {query.isFetchingNextPage ? "Loading…" : "Load more"}
        </Button>
      )}
      {query.isFetchNextPageError && <Alert>{errorMessage(query.error)}</Alert>}
    </section>
  );
}

export function VersionsPage({ ideaId }: { ideaId: string }) {
  const query = useQuery({ queryKey: ["versions", ideaId], queryFn: () => listVersions(ideaId) });
  return (
    <section className="max-w-3xl space-y-4">
      <h1 className="text-2xl font-semibold">Versions</h1>
      {query.isPending && <p role="status">Loading the versions…</p>}
      {query.isError && <Alert>{errorMessage(query.error)}</Alert>}
      {query.isSuccess && query.data.length === 0 && <p>This idea has no versions.</p>}
      {query.isSuccess && query.data.length > 0 && (
        <ul aria-label="Versions" className="space-y-3">
          {query.data.map((v: VersionSummary) => (
            <li key={v.version} className="space-y-2 rounded-lg border border-[var(--border)] p-4">
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="text-lg font-semibold">Version {v.version}</h2>
                {day(v.created_at)}
              </div>
              {v.change_note && <p className="text-sm">{v.change_note}</p>}
              <p className="line-clamp-3 text-sm text-[var(--muted)]">{v.text}</p>
              <Outcome verdict={v.verdict} score={v.score} stability={v.stability} />
              <div>
                <Link to="/ideas/$ideaId" params={{ ideaId }} search={{ v: v.version }} className="text-sm underline" aria-label={`Open card for version ${v.version}`}>
                  Open card
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
      <Link to="/history" className="text-sm underline">
        Back to history
      </Link>
    </section>
  );
}
