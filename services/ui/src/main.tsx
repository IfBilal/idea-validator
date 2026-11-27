import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { loadConfig } from "./lib/config";
import { makeRouter } from "./router";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root missing");

try {
  loadConfig(); // fail loudly and visibly when /config.js is wrong
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: 1, staleTime: 30_000 } },
  });
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={makeRouter()} />
      </QueryClientProvider>
    </StrictMode>,
  );
} catch (error) {
  root.textContent = error instanceof Error ? error.message : "The app could not start.";
}
