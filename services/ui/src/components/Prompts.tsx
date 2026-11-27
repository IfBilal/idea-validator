import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Alert, Field, Input } from "@/components/ui/form";
import { answerQuestion, decideApproval } from "@/lib/endpoints";
import { errorMessage } from "@/lib/errors";
import type { Approval, Question } from "@/lib/runState";

/** A clarifying question (U5): numeric, with the allowed range and why the answer matters. */
export function QuestionCard({ runId, question }: { runId: string; question: Question }) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string>();
  const send = useMutation({ mutationFn: (value: number) => answerQuestion(runId, question.questionId, value) });
  const [lo, hi] = question.range ?? [Number.NEGATIVE_INFINITY, Number.POSITIVE_INFINITY];

  function submit(event: FormEvent) {
    event.preventDefault();
    const value = text.trim() === "" ? Number.NaN : Number(text);
    if (!Number.isFinite(value)) return setError("Enter a number");
    if (value < lo || value > hi) return setError(`Enter a number between ${lo} and ${hi}`);
    setError(undefined);
    send.mutate(value);
  }

  return (
    <form onSubmit={submit} noValidate aria-label="Question" className="space-y-3 rounded-lg border border-[var(--accent)] p-4">
      <Field label={question.text} error={error}>
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            inputMode="decimal"
            aria-describedby={describedBy}
            aria-invalid={invalid}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        )}
      </Field>
      {question.why && <p className="text-sm text-[var(--muted)]">Why we ask: {question.why}</p>}
      {!question.required && (
        <p className="text-xs text-[var(--muted)]">Optional: if you skip it, a conservative default is used and listed on the result.</p>
      )}
      {send.isError && <Alert>{errorMessage(send.error)}</Alert>}
      {send.isSuccess && <Alert tone="info">Thanks, continuing…</Alert>}
      <Button type="submit" disabled={send.isPending || send.isSuccess}>
        Answer
      </Button>
    </form>
  );
}

/** The user decides; nothing happens until they do (U4). */
export function ApprovalDialog({ runId, approval }: { runId: string; approval: Approval }) {
  const decide = useMutation({
    mutationFn: (decision: "approve" | "decline") => decideApproval(runId, approval.approvalId, decision),
  });
  return (
    <div role="dialog" aria-modal="false" aria-label="Approval needed" className="space-y-3 rounded-lg border border-amber-500 p-4">
      <p>
        {approval.description || approval.action} would cost about <strong>${approval.estimatedCostUsd.toFixed(2)}</strong>{" "}
        (spent ${approval.spentUsd.toFixed(2)} of ${approval.budgetUsd.toFixed(2)}).
      </p>
      <p className="text-sm text-[var(--muted)]">
        If you decline, the run continues with the evidence it already has. Nothing is spent until you choose.
      </p>
      {decide.isError && <Alert>{errorMessage(decide.error)}</Alert>}
      <div className="flex gap-3">
        <Button disabled={decide.isPending || decide.isSuccess} onClick={() => decide.mutate("approve")}>
          Approve
        </Button>
        <Button variant="outline" disabled={decide.isPending || decide.isSuccess} onClick={() => decide.mutate("decline")}>
          Decline
        </Button>
      </div>
    </div>
  );
}
