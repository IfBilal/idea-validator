import { z } from "zod";
import { apiFetch } from "./api";
import { Accepted, LoginResponse, Profile, ProfileInput, RegisterResponse, ValidateAccepted } from "./schemas";

export const login = (email: string, password: string) =>
  apiFetch("/auth/login", LoginResponse, { method: "POST", json: { email, password } });

export const register = (email: string, password: string) =>
  apiFetch("/auth/register", RegisterResponse, { method: "POST", json: { email, password } });

export const getProfile = () => apiFetch("/profile", Profile);

export const putProfile = (profile: ProfileInput) =>
  apiFetch("/profile", Profile, { method: "PUT", json: profile });



/** `key` makes a double click or a retry start one run, not two (gateway Idempotency-Key). */
export const startValidate = (body: { idea_text: string; title: string; idea_id: string | null }, key: string) =>
  apiFetch("/validate", ValidateAccepted, { method: "POST", json: body, headers: { "Idempotency-Key": key } });

export const answerQuestion = (runId: string, questionId: string, answer: number) =>
  apiFetch(`/runs/${encodeURIComponent(runId)}/answer`, Accepted, {
    method: "POST",
    json: { question_id: questionId, answer },
  });

export const decideApproval = (runId: string, approvalId: string, decision: "approve" | "decline") =>
  apiFetch(`/runs/${encodeURIComponent(runId)}/approve`, Accepted, {
    method: "POST",
    json: { approval_id: approvalId, decision },
  });

const RunDetail = z.object({ result: z.unknown().optional() }).passthrough();

/** The run's stored result: a verdict card, or a partial card when no verdict could be given. */
export const getRunResult = async (runId: string): Promise<unknown> =>
  (await apiFetch(`/runs/${encodeURIComponent(runId)}`, RunDetail)).result;

import { RunAccepted } from "./schemas";

const ideaPath = (ideaId: string) => `/ideas/${encodeURIComponent(ideaId)}`;

/** `count` is 1..10; `niche` null means "anything that fits my profile". */
export const startDiscover = (body: { niche: string | null; count: number; deep_search: boolean }, key: string) =>
  apiFetch("/discover", RunAccepted, { method: "POST", json: body, headers: { "Idempotency-Key": key } });

/** One stored version of an idea. The shape is validated by the card parser that renders it. */
export const getIdeaVersion = (ideaId: string, version: number): Promise<unknown> =>
  apiFetch(`${ideaPath(ideaId)}/versions/${version}`, z.unknown());

/** Challenge one claim of a stored verdict version (U8). `key` makes a double click start one run. */
export const startChallenge = (ideaId: string, body: { version: number; claim_id: string }, key: string) =>
  apiFetch(`${ideaPath(ideaId)}/challenge`, RunAccepted, { method: "POST", json: body, headers: { "Idempotency-Key": key } });

/** What-if: exactly one of change_id (a suggested change) or custom_change (free text, at most 1000 characters). */
export const startWhatIf = (
  ideaId: string,
  body: { version: number; change_id: string } | { version: number; custom_change: string },
  key: string,
) => apiFetch(`${ideaPath(ideaId)}/whatif`, RunAccepted, { method: "POST", json: body, headers: { "Idempotency-Key": key } });
