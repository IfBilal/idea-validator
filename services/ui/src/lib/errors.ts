import { ApiError } from "./api";

/** Plain-language text for a failed call. Never shows server internals; keeps the request id for support. */
export function errorMessage(error: unknown, overrides: { conflict?: string } = {}): string {
  if (!(error instanceof ApiError)) return "Something went wrong. Please try again.";
  switch (error.code) {
    case "NETWORK":
      return error.message;
    case "RATE_LIMITED":
      return error.retryAfterSeconds !== undefined
        ? `Too many attempts. Please wait ${error.retryAfterSeconds} seconds and try again.`
        : "Too many attempts. Please wait a moment and try again.";
    case "UNAUTHORIZED":
      return "Email or password is incorrect.";
    case "CONFLICT":
      return overrides.conflict ?? "This idea changed in the meantime. Reload it and try again.";
    case "VALIDATION_ERROR":
      return `Please check what you entered. ${error.message}`;
    default:
      return error.requestId
        ? `Something went wrong on our side (reference ${error.requestId}). Please try again.`
        : "Something went wrong on our side. Please try again.";
  }
}
