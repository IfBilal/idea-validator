import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { makeRouter } from "@/router";

function renderAt(path: string) {
  const router = makeRouter(createMemoryHistory({ initialEntries: [path] }));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("app shell", () => {
  it("shows the home screen with the honesty line", async () => {
    renderAt("/");
    expect(await screen.findByRole("heading", { name: /should you build it/i })).toBeInTheDocument();
    expect(screen.getByText(/decision support, not a prediction of success/i)).toBeInTheDocument();
  });

  it("navigates between screens from the main navigation", async () => {
    renderAt("/");
    const nav = await screen.findByRole("navigation", { name: /main/i });
    await userEvent.click(nav.querySelector('a[href="/ideas"]') as HTMLElement);
    expect(await screen.findByRole("heading", { name: /my ideas/i })).toBeInTheDocument();
  });

  it("shows a friendly page for unknown routes", async () => {
    renderAt("/nope");
    expect(await screen.findByRole("heading", { name: /page not found/i })).toBeInTheDocument();
  });
});
