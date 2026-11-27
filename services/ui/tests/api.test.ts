import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { z } from "zod";
import { ApiError, apiFetch, setTokenProvider } from "@/lib/api";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  server.resetHandlers();
  setTokenProvider(() => null);
});
afterAll(() => server.close());
beforeEach(() => {
  window.__APP_CONFIG__ = { API_BASE_URL: "http://gw.test" };
});

const Profile = z.object({ hours_per_week: z.number() });

describe("apiFetch", () => {
  it("returns validated JSON and sends the bearer token", async () => {
    let auth: string | null = null;
    server.use(
      http.get("http://gw.test/profile", ({ request }) => {
        auth = request.headers.get("Authorization");
        return HttpResponse.json({ hours_per_week: 10 });
      }),
    );
    setTokenProvider(() => "tok");
    expect(await apiFetch("/profile", Profile)).toEqual({ hours_per_week: 10 });
    expect(auth).toBe("Bearer tok");
  });

  it("posts JSON bodies with a content type", async () => {
    let body: unknown;
    server.use(
      http.post("http://gw.test/validate", async ({ request }) => {
        body = await request.json();
        expect(request.headers.get("Content-Type")).toBe("application/json");
        return HttpResponse.json({ hours_per_week: 1 }, { status: 202 });
      }),
    );
    await apiFetch("/validate", Profile, { method: "POST", json: { idea_text: "x" } });
    expect(body).toEqual({ idea_text: "x" });
  });

  it("maps the gateway error envelope, including request id and Retry-After", async () => {
    server.use(
      http.get("http://gw.test/profile", () =>
        HttpResponse.json(
          { error: { code: "RATE_LIMITED", message: "Slow down", request_id: "req_1" } },
          { status: 429, headers: { "Retry-After": "7" } },
        ),
      ),
    );
    const err = await apiFetch("/profile", Profile).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({
      status: 429,
      code: "RATE_LIMITED",
      message: "Slow down",
      requestId: "req_1",
      retryAfterSeconds: 7,
    });
  });

  it("does not trust a non-envelope error body", async () => {
    server.use(http.get("http://gw.test/profile", () => new HttpResponse("<html>boom</html>", { status: 502 })));
    const err = await apiFetch("/profile", Profile).catch((e: unknown) => e);
    expect(err).toMatchObject({ status: 502, code: "BAD_RESPONSE" });
    expect((err as ApiError).message).not.toContain("boom");
  });

  it("rejects a success body that fails the schema", async () => {
    server.use(http.get("http://gw.test/profile", () => HttpResponse.json({ nope: true })));
    await expect(apiFetch("/profile", Profile)).rejects.toMatchObject({ code: "BAD_RESPONSE" });
  });

  it("reports an unreachable server as NETWORK", async () => {
    server.use(http.get("http://gw.test/profile", () => HttpResponse.error()));
    await expect(apiFetch("/profile", Profile)).rejects.toMatchObject({ status: 0, code: "NETWORK" });
  });

  it("ignores a malformed Retry-After header", async () => {
    server.use(
      http.get("http://gw.test/profile", () =>
        HttpResponse.json(
          { error: { code: "RATE_LIMITED", message: "m" } },
          { status: 429, headers: { "Retry-After": "soon" } },
        ),
      ),
    );
    const err = (await apiFetch("/profile", Profile).catch((e: unknown) => e)) as ApiError;
    expect(err.retryAfterSeconds).toBeUndefined();
  });
});
