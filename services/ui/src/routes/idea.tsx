import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Alert } from "@/components/ui/form";
import { VerdictCard } from "@/components/VerdictCard";
import { getIdeaVersion } from "@/lib/endpoints";
import { errorMessage } from "@/lib/errors";

/**
 * The gateway returns a stored version as `{idea_id, version, title, text, verdict, ..., card}`; `card` is the
 * verdict card (null until validated). A bare card (no `card` key) is accepted too.
 */
export function cardOf(raw: unknown): unknown {
  if (typeof raw === "object" && raw !== null && !Array.isArray(raw) && "card" in raw) {
    return (raw as { card: unknown }).card;
  }
  return raw;
}

export function IdeaPage({ ideaId, version }: { ideaId: string; version: number }) {
  const query = useQuery({ queryKey: ["idea-version", ideaId, version], queryFn: () => getIdeaVersion(ideaId, version) });
  const card = query.data === undefined ? undefined : cardOf(query.data);
  return (
    <section className="max-w-3xl space-y-4">
      <h1 className="text-2xl font-semibold">
        Idea card <span className="text-base font-normal text-[var(--muted)]">v{version}</span>
      </h1>
      {query.isPending && <p role="status">Loading the card…</p>}
      {query.isError && <Alert>{errorMessage(query.error)}</Alert>}
      {query.isSuccess && (card === null || card === undefined ? <Alert tone="info">No verdict card is stored for this version yet.</Alert> : <VerdictCard raw={card} />)}
      <Link to="/discover" className="text-sm underline">
        Back to discover
      </Link>
    </section>
  );
}
