import { z } from "zod";
import { loadConfig } from "./config";

const ErrorEnvelope = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    details: z.unknown().optional(),
    request_id: z.string().optional(),
  }),
});

/** A failed gateway call. `code` is the envelope code (or NETWORK / BAD_RESPONSE for non-envelopes). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;
  readonly retryAfterSeconds?: number;

  constructor(
    status: number,
    code: string,
    message: string,
    requestId?: string,
    retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export type TokenProvider = () => string | null;
let tokenProvider: TokenProvider = () => null;

/** The auth slice registers where the access token lives; until then calls are anonymous. */
export function setTokenProvider(provider: TokenProvider): void {
  tokenProvider = provider;
}

function retryAfter(response: Response): number | undefined {
  const raw = response.headers.get("Retry-After");
  const seconds = raw === null ? Number.NaN : Number.parseInt(raw, 10);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : undefined;
}

function parseJson(text: string): unknown {
  if (text === "") return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export type ApiInit = Omit<RequestInit, "body"> & { json?: unknown };

/** Call the gateway (the only backend the browser talks to) and validate the reply with zod. */
export async function apiFetch<T>(path: string, schema: z.ZodType<T>, init: ApiInit = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const merged = new Headers(headers);
  merged.set("Accept", "application/json");
  if (json !== undefined) merged.set("Content-Type", "application/json");
  const token = tokenProvider();
  if (token) merged.set("Authorization", `Bearer ${token}`);

  let response: Response;
  try {
    response = await fetch(`${loadConfig().API_BASE_URL}${path}`, {
      ...rest,
      headers: merged,
      body: json === undefined ? undefined : JSON.stringify(json),
    });
  } catch {
    throw new ApiError(0, "NETWORK", "Cannot reach the server. Check your connection and try again.");
  }

  const text = await response.text();
  const body = parseJson(text);

  if (!response.ok) {
    const envelope = ErrorEnvelope.safeParse(body);
    if (envelope.success) {
      const { code, message, request_id } = envelope.data.error;
      throw new ApiError(response.status, code, message, request_id, retryAfter(response));
    }
    throw new ApiError(
      response.status,
      "BAD_RESPONSE",
      `Unexpected server response (HTTP ${response.status}).`,
    );
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    throw new ApiError(
      response.status,
      "BAD_RESPONSE",
      "The server sent a response this app does not understand.",
    );
  }
  return parsed.data;
}
