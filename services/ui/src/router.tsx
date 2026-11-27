import {
  Link,
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
  useRouter,
  type RouterHistory,
} from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { clearSession, getToken, safeRedirect, useIsAuthenticated } from "@/lib/auth";
import { LoginPage, RegisterPage } from "@/routes/auth";
import { ProfilePage } from "@/routes/profile";

function Shell() {
  const authed = useIsAuthenticated();
  const router = useRouter();
  return (
    <div className="mx-auto max-w-5xl px-4">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--border)] py-4">
        <Link to="/" className="text-lg font-semibold">
          Idea Validator
        </Link>
        <nav aria-label="Main" className="flex flex-wrap items-center gap-4 text-sm text-[var(--muted)]">
          {authed ? (
            <>
              <Link to="/validate">Validate</Link>
              <Link to="/discover">Discover</Link>
              <Link to="/ideas">My ideas</Link>
              <Link to="/profile">Profile</Link>
              <button
                type="button"
                className="underline"
                onClick={() => {
                  clearSession();
                  router.history.push("/login");
                }}
              >
                Log out
              </button>
            </>
          ) : (
            <>
              <Link to="/login" search={{}}>Log in</Link>
              <Link to="/register">Create account</Link>
            </>
          )}
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
  const authed = useIsAuthenticated();
  return (
    <section className="space-y-4">
      <h1 className="text-3xl font-bold">Should you build it?</h1>
      <p className="max-w-prose text-[var(--muted)]">
        Validate an idea against real evidence, or let the system discover ideas that fit your skills and
        time. Every verdict comes from a deterministic engine and every claim links to its sources.
      </p>
      <div className="flex gap-3">
        {authed ? (
          <>
            <Button asChild>
              <Link to="/validate">Validate an idea</Link>
            </Button>
            <Button asChild variant="outline">
              <Link to="/discover">Discover ideas</Link>
            </Button>
          </>
        ) : (
          <>
            <Button asChild>
              <Link to="/register">Get started</Link>
            </Button>
            <Button asChild variant="outline">
              <Link to="/login" search={{}}>Log in</Link>
            </Button>
          </>
        )}
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

/** Screens that need a session send anonymous visitors to /login and bring them back afterwards. */
function requireAuth({ location }: { location: { href: string } }) {
  if (getToken() === null) {
    throw redirect({ to: "/login", search: { redirect: location.href } });
  }
}

/** A logged-in user has no business on the login or register page. */
function redirectIfAuthed() {
  if (getToken() !== null) throw redirect({ to: "/profile" });
}

const protectedRoute = <P extends string>(path: P, component: () => React.JSX.Element) =>
  createRoute({ getParentRoute: () => rootRoute, path, component, beforeLoad: requireAuth });

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: (search: Record<string, unknown>): { redirect?: string } =>
    typeof search.redirect === "string" ? { redirect: safeRedirect(search.redirect) } : {},
  beforeLoad: redirectIfAuthed,
  component: function Login() {
    const { redirect: target } = loginRoute.useSearch();
    return <LoginPage redirectTo={target} />;
  },
});

export const routeTree = rootRoute.addChildren([
  createRoute({ getParentRoute: () => rootRoute, path: "/", component: Home }),
  loginRoute,
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/register",
    beforeLoad: redirectIfAuthed,
    component: RegisterPage,
  }),
  protectedRoute("/profile", ProfilePage),
  protectedRoute("/validate", () => <Placeholder title="Validate an idea" />),
  protectedRoute("/discover", () => <Placeholder title="Discover ideas" />),
  protectedRoute("/ideas", () => <Placeholder title="My ideas" />),
]);
export function makeRouter(history?: RouterHistory) {
  return createRouter({ routeTree, history });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof makeRouter>;
  }
}
