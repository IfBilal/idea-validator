import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearSession, getToken, resetAuthForTests, safeRedirect, setSession } from "@/lib/auth";

beforeEach(() => {
  window.sessionStorage.clear();
  resetAuthForTests();
});
afterEach(() => {
  vi.useRealTimers();
  clearSession();
});

describe("session store", () => {
  it("starts logged out", () => {
    expect(getToken()).toBeNull();
  });

  it("keeps the token in sessionStorage, never localStorage", () => {
    setSession("tok", 3600);
    expect(getToken()).toBe("tok");
    expect(window.sessionStorage.getItem("iv.session")).toContain("tok");
    expect(window.localStorage.length).toBe(0);
  });

  it("restores a stored session after a reload", () => {
    setSession("tok", 3600);
    resetAuthForTests();
    expect(getToken()).toBe("tok");
  });

  it("drops an expired token and clears storage", () => {
    vi.useFakeTimers();
    setSession("tok", 60);
    vi.advanceTimersByTime(61_000);
    expect(getToken()).toBeNull();
    expect(window.sessionStorage.getItem("iv.session")).toBeNull();
  });

  it("ignores a corrupt stored value", () => {
    window.sessionStorage.setItem("iv.session", "{not json");
    resetAuthForTests();
    expect(getToken()).toBeNull();
    window.sessionStorage.setItem("iv.session", JSON.stringify({ token: 5 }));
    resetAuthForTests();
    expect(getToken()).toBeNull();
  });

  it("logout clears everything", () => {
    setSession("tok", 3600);
    clearSession();
    expect(getToken()).toBeNull();
    expect(window.sessionStorage.getItem("iv.session")).toBeNull();
  });
});

describe("safeRedirect (no open redirects)", () => {
  it.each(["/profile", "/ideas/abc?x=1", "/"])("allows %s", (ok) => {
    expect(safeRedirect(ok)).toBe(ok);
  });

  it.each([
    "//evil.example",
    "https://evil.example",
    "javascript:alert(1)",
    "/\\evil.example",
    "/ok\nSet-Cookie: x",
    "",
    undefined,
    42,
    null,
  ])("rewrites %j to /", (bad) => {
    expect(safeRedirect(bad)).toBe("/");
  });
});
