# ui (M1 front end): React shell

Vite + React 19 + TypeScript + Tailwind v4 + TanStack Query/Router + zod. The browser talks only to the gateway
(`docs/harness/22_UI_UX_SPEC.md`, API contract in `services/gateway/README.md`).

| | |
|---|---|
| Port | 8080 (nginx-unprivileged, uid 101, read-only root filesystem) |
| Probe | `GET /healthz` (no `/ready`, no `/metrics`: the UI has no dependencies and nothing sensitive to expose) |
| Config | `API_BASE_URL` at **container start** (default `/api`, same origin behind the ingress); no rebuild needed |

## Runtime configuration
`index.html` loads `/config.js` before the app. nginx serves it from `/tmp/config.js`, written at start by
`nginx/40-runtime-config.sh` from `API_BASE_URL`. The value must be a path like `/api` or an `http(s)://` URL.
Anything else stops the container (it ends up inside JavaScript and a Content-Security-Policy, so it is validated, not
escaped). The same script writes the security headers; the CSP allows `connect-src 'self'` plus the API origin only.
The app re-validates the config with zod and shows the error instead of a blank page.

## Develop
```bash
cd services/ui && npm ci
DEV_API_TARGET=http://127.0.0.1:8000 npm run dev   # /api is proxied to a local gateway
npm run typecheck && npm run lint && npm test -- --run && npm run build
```
`make lint typecheck test` run the same checks (they skip the UI when `services/ui/package.json` is absent).

## Image
```bash
docker build -t ui:$(git rev-parse --short=7 HEAD) services/ui   # context = services/ui
```
Multi-stage (node build, nginx-unprivileged runtime), pinned bases, no `latest`. `scripts/gen_k8s.py` generates its
Deployment, Service, NetworkPolicy (only the ingress controller may call it, it calls nothing) and a separate Ingress for
`/` (the API ingress rewrites paths, so `/` cannot share it).

## Layout
`src/lib/config.ts` (runtime config), `src/lib/api.ts` (gateway client: bearer token, error envelope, Retry-After),
`src/router.tsx` (routes), `src/components/ui/` (shadcn-style primitives), `tests/` (Vitest + MSW + Testing Library).
Screens arrive slice by slice (issue #33): auth and profile, live run panel, verdict card, what-if and challenge, Discover,
history and compare, trace viewer, Playwright end to end.
