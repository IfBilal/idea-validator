import { useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { ApprovalDialog, QuestionCard } from "@/components/Prompts";
import { RunPanel } from "@/components/RunPanel";
import { VerdictCard } from "@/components/VerdictCard";
import { Button } from "@/components/ui/button";
import { Alert, Field, Input } from "@/components/ui/form";
import { getRunResult, startValidate } from "@/lib/endpoints";
import { errorMessage } from "@/lib/errors";
import { useRun } from "@/lib/useRun";

const MAX_IDEA = 4000;

export function ValidatePage() {
  const [title, setTitle] = useState("");
  const [idea, setIdea] = useState("");
  const [errors, setErrors] = useState<{ title?: string; idea?: string }>({});
  const key = useRef<string>(crypto.randomUUID()); // one key per submitted form: a retry starts one run
  const start = useMutation({ mutationFn: () => startValidate({ title: title.trim(), idea_text: idea.trim(), idea_id: null }, key.current) });

  function submit(event: FormEvent) {
    event.preventDefault();
    const next: typeof errors = {};
    if (title.trim() === "") next.title = "Give your idea a short title";
    else if (title.trim().length > 200) next.title = "At most 200 characters";
    if (idea.trim().length < 10) next.idea = "Describe the idea in at least a sentence";
    else if (idea.trim().length > MAX_IDEA) next.idea = `At most ${MAX_IDEA} characters`;
    setErrors(next);
    if (Object.keys(next).length === 0) start.mutate();
  }

  const runId = start.data?.run_id ?? null;
  if (runId) return <RunView runId={runId} />;

  return (
    <section className="max-w-2xl space-y-4">
      <h1 className="text-2xl font-semibold">Validate an idea</h1>
      <form onSubmit={submit} noValidate className="space-y-4">
        <Field label="Title" error={errors.title}>
          {({ id, describedBy, invalid }) => (
            <Input id={id} aria-describedby={describedBy} aria-invalid={invalid} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
          )}
        </Field>
        <Field label="Your idea" hint={`${idea.length} / ${MAX_IDEA}`} error={errors.idea}>
          {({ id, describedBy, invalid }) => (
            <textarea
              id={id}
              rows={6}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-sm aria-[invalid=true]:border-red-500"
            />
          )}
        </Field>
        {start.isError && <Alert>{errorMessage(start.error)}</Alert>}
        <Button type="submit" disabled={start.isPending}>
          {start.isPending ? "Starting…" : "Validate"}
        </Button>
      </form>
    </section>
  );
}

function RunView({ runId }: { runId: string }) {
  const { state, status } = useRun(runId);
  return (
    <section className="max-w-2xl space-y-4">
      <h1 className="text-2xl font-semibold">Validating your idea</h1>
      <RunPanel state={state} status={status} />
      {state.phase === "waiting_answer" && state.question && <QuestionCard key={state.question.questionId} runId={runId} question={state.question} />}
      {state.phase === "waiting_approval" && state.approval && <ApprovalDialog key={state.approval.approvalId} runId={runId} approval={state.approval} />}
      {state.phase === "done" && <FinalResult runId={runId} streamed={state.verdictCard} />}
    </section>
  );
}

/** The verdict event carries a settled card; a partial card (no verdict) only arrives in the run result. */
function FinalResult({ runId, streamed }: { runId: string; streamed: unknown }) {
  const result = useQuery({ queryKey: ["run-result", runId], queryFn: () => getRunResult(runId), enabled: streamed === undefined });
  if (streamed !== undefined) return <VerdictCard raw={streamed} />;
  if (result.isPending) return <p role="status">Loading your result…</p>;
  if (result.isError) return <Alert>{errorMessage(result.error)}</Alert>;
  return <VerdictCard raw={result.data} />;
}
