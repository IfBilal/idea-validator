import { useSyncExternalStore } from "react";
import { setTokenProvider } from "./api";

/**
 * The access token lives in memory and in sessionStorage (cleared when the tab closes, never in
 * localStorage). The strict CSP (script-src 'self') is the XSS mitigation; tokens expire after an hour
 * and are dropped client-side the moment they do. There is no refresh token (the gateway has none).
 */
const KEY = "iv.session";

interface Session {
  token: string;
  expiresAt: number; // epoch ms
}

function readStored(): Session | null {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      typeof (parsed as Session).token === "string" &&
      typeof (parsed as Session).expiresAt === "number"
    ) {
      return parsed as Session;
    }
  } catch {
    // storage blocked or corrupt: behave as logged out
  }
  return null;
}

function writeStored(session: Session | null): void {
  try {
    if (session) window.sessionStorage.setItem(KEY, JSON.stringify(session));
    else window.sessionStorage.removeItem(KEY);
  } catch {
    // storage blocked: the in-memory session still works for this page load
  }
}

let current: Session | null = readStored();
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((l) => l());
}

export function getToken(): string | null {
  if (current && current.expiresAt <= Date.now()) {
    current = null;
    writeStored(null);
    queueMicrotask(emit);
  }
  return current?.token ?? null;
}

export function setSession(token: string, expiresInSeconds: number): void {
  current = { token, expiresAt: Date.now() + expiresInSeconds * 1000 };
  writeStored(current);
  emit();
}

export function clearSession(): void {
  current = null;
  writeStored(null);
  emit();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useIsAuthenticated(): boolean {
  return useSyncExternalStore(subscribe, () => getToken() !== null);
}

/** Test helper: forget in-memory state and re-read storage. */
export function resetAuthForTests(): void {
  current = readStored();
  listeners.clear();
}

setTokenProvider(getToken);

/** Only same-site absolute paths are allowed as a post-login destination (no open redirect). */
export function safeRedirect(target: unknown): string {
  if (typeof target !== "string") return "/";
  if (!target.startsWith("/") || target.startsWith("//") || target.includes("\\")) return "/";
  if ([...target].some((c) => c.charCodeAt(0) < 32)) return "/"; // control characters
  return target;
}
