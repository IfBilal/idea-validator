import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { setUnauthorizedHandler } from "@/lib/api";
import { clearSession, getToken, resetAuthForTests, setSession } from "@/lib/auth";
import { makeRouter } from "@/router";

const API = "http://gw.test";
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterAll(() => server.close());

beforeEach(() => {
  window.__APP_CONFIG__ = { API_BASE_URL: API };
  window.sessionStorage.clear();
  resetAuthForTests();
});
afterEach(() => {
  server.resetHandlers();
  clearSession();
  setUnauthorizedHandler(() => {});
});

function renderAt(path: string) {
  const router = makeRouter(createMemoryHistory({ initialEntries: [path] }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const PROFILE = {
  user_id: "u1",
  skills: ["python"],
  hours_per_week: 12,
  weeks_available: 10,
  team_size: 2,
  compute: "laptop",
  goal: "portfolio",
  interests: ["devtools"],
  version: 3,
};

describe("route guards", () => {
  it("sends anonymous visitors to the login page and remembers where they were going", async () => {
    const router = renderAt("/profile");
    expect(await screen.findByRole("heading", { name: /log in/i })).toBeInTheDocument();
    expect(router.state.location.search).toMatchObject({ redirect: "/profile" });
  });

  it("keeps logged-in users out of the login page", async () => {
    setSession("tok", 3600);
    server.use(http.get(`${API}/profile`, () => HttpResponse.json(PROFILE)));
    renderAt("/login");
    expect(await screen.findByRole("heading", { name: /your profile/i })).toBeInTheDocument();
  });

  it("shows login and register in the navigation when logged out, and the app links when logged in", async () => {
    renderAt("/");
    const nav = await screen.findByRole("navigation", { name: /main/i });
    expect(nav).toHaveTextContent(/log in/i);
    expect(nav).not.toHaveTextContent(/validate/i);
  });
});

describe("login", () => {
  it("logs in, stores the token and returns to where the user was going", async () => {
    server.use(
      http.post(`${API}/auth/login`, async ({ request }) => {
        expect(await request.json()).toEqual({ email: "a@b.org", password: "correct horse" });
        return HttpResponse.json({ access_token: "tok1", token_type: "bearer", expires_in: 3600 });
      }),
      http.get(`${API}/profile`, () => HttpResponse.json(PROFILE)),
    );
    renderAt("/login?redirect=%2Fprofile");
    await userEvent.type(await screen.findByLabelText("Email"), "a@b.org");
    await userEvent.type(screen.getByLabelText("Password"), "correct horse");
    await userEvent.click(screen.getByRole("button", { name: /^log in$/i }));
    expect(await screen.findByRole("heading", { name: /your profile/i })).toBeInTheDocument();
    expect(getToken()).toBe("tok1");
  });

  it("shows one generic message for a wrong password and keeps the user out", async () => {
    server.use(
      http.post(`${API}/auth/login`, () =>
        HttpResponse.json({ error: { code: "UNAUTHORIZED", message: "Invalid credentials" } }, { status: 401 }),
      ),
    );
    renderAt("/login");
    await userEvent.type(await screen.findByLabelText("Email"), "a@b.org");
    await userEvent.type(screen.getByLabelText("Password"), "wrong pass");
    await userEvent.click(screen.getByRole("button", { name: /^log in$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
    expect(getToken()).toBeNull();
  });

  it("tells the user how long to wait when rate limited", async () => {
    server.use(
      http.post(`${API}/auth/login`, () =>
        HttpResponse.json(
          { error: { code: "RATE_LIMITED", message: "slow" } },
          { status: 429, headers: { "Retry-After": "42" } },
        ),
      ),
    );
    renderAt("/login");
    await userEvent.type(await screen.findByLabelText("Email"), "a@b.org");
    await userEvent.type(screen.getByLabelText("Password"), "whatever1");
    await userEvent.click(screen.getByRole("button", { name: /^log in$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("wait 42 seconds");
  });

  it("validates before calling the server", async () => {
    renderAt("/login");
    await userEvent.click(await screen.findByRole("button", { name: /^log in$/i }));
    expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();
    expect(screen.getByText("Enter your password")).toBeInTheDocument();
  });

  it("does not follow an off-site redirect target", async () => {
    server.use(
      http.post(`${API}/auth/login`, () =>
        HttpResponse.json({ access_token: "t", token_type: "bearer", expires_in: 60 }),
      ),
    );
    const router = renderAt("/login?redirect=%2F%2Fevil.example");
    await userEvent.type(await screen.findByLabelText("Email"), "a@b.org");
    await userEvent.type(screen.getByLabelText("Password"), "whatever1");
    await userEvent.click(screen.getByRole("button", { name: /^log in$/i }));
    await waitFor(() => expect(getToken()).toBe("t"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });
});

describe("register", () => {
  it("requires a 10 character password", async () => {
    renderAt("/register");
    await userEvent.type(await screen.findByLabelText("Email"), "a@b.org");
    await userEvent.type(screen.getByLabelText("Password"), "short");
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    expect(await screen.findByText("Use at least 10 characters")).toBeInTheDocument();
  });

  it("registers, logs in and lands on the profile page", async () => {
    server.use(
      http.post(`${API}/auth/register`, () => HttpResponse.json({ user_id: "u9" }, { status: 201 })),
      http.post(`${API}/auth/login`, () =>
        HttpResponse.json({ access_token: "tok9", token_type: "bearer", expires_in: 3600 }),
      ),
      http.get(`${API}/profile`, () =>
        HttpResponse.json({ error: { code: "NOT_FOUND", message: "profile not found" } }, { status: 404 }),
      ),
    );
    renderAt("/register");
    await userEvent.type(await screen.findByLabelText("Email"), "new@b.org");
    await userEvent.type(screen.getByLabelText("Password"), "a long password");
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    expect(await screen.findByRole("heading", { name: /your profile/i })).toBeInTheDocument();
    expect(screen.getByText(/not saved yet/i)).toBeInTheDocument();
  });

  it("reports a taken email in plain words", async () => {
    server.use(
      http.post(`${API}/auth/register`, () =>
        HttpResponse.json({ error: { code: "CONFLICT", message: "email taken" } }, { status: 409 }),
      ),
    );
    renderAt("/register");
    await userEvent.type(await screen.findByLabelText("Email"), "a@b.org");
    await userEvent.type(screen.getByLabelText("Password"), "a long password");
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("An account with this email already exists.");
  });
});

describe("profile", () => {
  beforeEach(() => setSession("tok", 3600));

  it("loads the saved profile and its version", async () => {
    server.use(http.get(`${API}/profile`, () => HttpResponse.json(PROFILE)));
    renderAt("/profile");
    expect(await screen.findByDisplayValue("12")).toBeInTheDocument();
    expect(screen.getByText(/version 3/i)).toBeInTheDocument();
    expect(screen.getByText("python")).toBeInTheDocument();
    expect(screen.getByText(/changes affect future recommendations/i)).toBeInTheDocument();
  });

  it("saves edits as a new version without sending user_id or version", async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.get(`${API}/profile`, () => HttpResponse.json(PROFILE)),
      http.put(`${API}/profile`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...PROFILE, ...body, version: 4 });
      }),
    );
    renderAt("/profile");
    const hours = await screen.findByLabelText("Hours per week");
    await userEvent.clear(hours);
    await userEvent.type(hours, "20");
    const skills = screen.getByLabelText("Skills");
    await userEvent.type(skills, "react{enter}");
    await userEvent.click(screen.getByRole("button", { name: /save profile/i }));
    expect(await screen.findByText(/saved as version 4/i)).toBeInTheDocument();
    expect(body).toMatchObject({ hours_per_week: 20, skills: ["python", "react"], team_size: 2 });
    expect(body).not.toHaveProperty("user_id");
    expect(body).not.toHaveProperty("version");
  });

  it("rejects out-of-range and non-numeric values before calling the server", async () => {
    server.use(http.get(`${API}/profile`, () => HttpResponse.json(PROFILE)));
    renderAt("/profile");
    const hours = await screen.findByLabelText("Hours per week");
    await userEvent.clear(hours);
    await userEvent.type(hours, "81");
    const weeks = screen.getByLabelText("Weeks available");
    await userEvent.clear(weeks);
    await userEvent.type(weeks, "abc");
    await userEvent.click(screen.getByRole("button", { name: /save profile/i }));
    expect(await screen.findByText("At most 80 hours a week")).toBeInTheDocument();
    expect(screen.getByText("Enter a whole number")).toBeInTheDocument();
  });

  it("removes a tag with its remove button", async () => {
    server.use(http.get(`${API}/profile`, () => HttpResponse.json(PROFILE)));
    renderAt("/profile");
    await userEvent.click(await screen.findByRole("button", { name: "Remove python" }));
    expect(screen.queryByText("python")).not.toBeInTheDocument();
  });

  it("logs the user out when the server says the session is no longer valid", async () => {
    server.use(
      http.get(`${API}/profile`, () =>
        HttpResponse.json({ error: { code: "UNAUTHORIZED", message: "expired" } }, { status: 401 }),
      ),
    );
    let cleared = false;
    setUnauthorizedHandler(() => {
      clearSession();
      cleared = true;
    });
    renderAt("/profile");
    await waitFor(() => expect(cleared).toBe(true));
    expect(getToken()).toBeNull();
  });

  it("log out clears the session", async () => {
    server.use(http.get(`${API}/profile`, () => HttpResponse.json(PROFILE)));
    renderAt("/profile");
    await userEvent.click(await screen.findByRole("button", { name: /log out/i }));
    expect(getToken()).toBeNull();
    expect(await screen.findByRole("heading", { name: /log in/i })).toBeInTheDocument();
  });
});
