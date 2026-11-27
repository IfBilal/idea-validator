import {
  Link,
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  type RouterHistory,
} from "@tanstack/react-router";
import { Button } from "@/components/ui/button";

function Shell() {
  return (
    <div className="mx-auto max-w-5xl px-4">
      <header className="flex items-center justify-between gap-4 border-b border-[var(--border)] py-4">
        <Link to="/" className="text-lg font-semibold">
          Idea Validator
        </Link>
        <nav aria-label="Main" className="flex gap-4 text-sm text-[var(--muted)]">
          <Link to="/validate">Validate</Link>
          <Link to="/discover">Discover</Link>
          <Link to="/ideas">My ideas</Link>
        </nav>
      </header>
      <main className="py-8">
        <Outlet />
      </main>
      <footer className="border-t border-[var(--border)] py-4 text-xs text-[var(--muted)]">
        Decision support, not a prediction of success.
      </footer>
    </div>
  );
}

function Home() {
  return (
    <section className="space-y-4">
      <h1 className="text-3xl font-bold">Should you build it?</h1>
      <p className="max-w-prose text-[var(--muted)]">
        Validate an idea against real evidence, or let the system discover ideas that fit your skills and
        time. Every verdict comes from a deterministic engine and every claim links to its sources.
      </p>
      <div className="flex gap-3">
        <Button asChild>
          <Link to="/validate">Validate an idea</Link>
        </Button>
        <Button asChild variant="outline">
          <Link to="/discover">Discover ideas</Link>
        </Button>
      </div>
    </section>
  );
}

function Placeholder({ title }: { title: string }) {
  return (
    <section>
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="text-[var(--muted)]">This screen arrives in a later slice.</p>
    </section>
  );
}

function NotFound() {
  return (
    <section className="space-y-2">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <Link to="/" className="underline">
        Back to the start
      </Link>
    </section>
  );
}

const rootRoute = createRootRoute({ component: Shell, notFoundComponent: NotFound });

const screens = [
  createRoute({ getParentRoute: () => rootRoute, path: "/", component: Home }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/validate",
    component: () => <Placeholder title="Validate an idea" />,
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/discover",
    component: () => <Placeholder title="Discover ideas" />,
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/ideas",
    component: () => <Placeholder title="My ideas" />,
  }),
];

export const routeTree = rootRoute.addChildren(screens);

export function makeRouter(history?: RouterHistory) {
  return createRouter({ routeTree, history });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof makeRouter>;
  }
}
