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
