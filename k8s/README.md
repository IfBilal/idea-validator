# Kubernetes manifests

Namespace `idea-validator` (Pod Security `restricted`). Layout: `base/` (everything) and `overlays/dev/` (kind: one replica,
images from `localhost:5001`). Most of `base/` is **generated** from the table in `scripts/gen_k8s.py`: edit the table, run
`python scripts/gen_k8s.py`, commit. A test and a CI job fail if the files drift from the generator. Hand-written:
`base/netpol/default-deny.yaml` (default-deny, DNS, UI egress).

```
make k8s-up                 # kind + Calico + ingress-nginx + metrics-server (pins its own kubeconfig)
python scripts/gen_k8s.py   # regenerate manifests after editing the table
kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/overlays/dev   # config/ lives outside k8s/, hence the flag
```

## Run the whole stack on kind
```
make k8s-up        # once: cluster + Calico + ingress + metrics-server (needs fs.inotify.max_user_instances >= 512)
make images TAG=dev          # build and push the 13 images to localhost:5001
make deploy TAG=dev          # secrets (from .env) -> manifests -> datastores -> migrate Job -> all Deployments
make smoke                   # register, login, save and read a profile through the ingress, ops endpoints hidden
make k8s-verify              # registry, ingress, metrics-server and that Calico enforces NetworkPolicy
```
`make secrets` creates `app-secrets` on its own (random values are generated once and kept on re-runs, so the database
password never changes under a running database; values are never printed). Every script pins `~/.kube/idea-validator.kubeconfig`
and refuses any other context.

What is enforced (and tested in `libs/common/tests/test_k8s_manifests.py`): non-root UID 10001, read-only root filesystem, all
capabilities dropped, seccomp RuntimeDefault, no service-account token, probes on `/health` and `/ready`, rolling update with
`maxUnavailable: 0`, no `:latest`, no Secret in git, one NetworkPolicy per workload derived from the call graph (a service accepts
traffic only from its callers and may reach only its callees; every service may post traces to `agentops`), and only `tools` has
internet egress (TCP 443, private ranges excluded).

Secrets are never committed: `app-secrets` (keys `INTERNAL_TOKEN`, `JWT_SECRET`, `DATABASE_URL`, `GROQ_API_KEY`,
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `REDIS_PASSWORD`, `REDIS_URL`, `TOOLS_REDIS_PASSWORD`, `TOOLS_REDIS_URL`) is created by `scripts/create_secrets.sh` (P1-T06) from the git-ignored `.env`.
Each service receives only the keys it uses. There are two Redis instances, both with a password: `redis` (gateway, profile, orchestrator, kb) and `redis-tools` (only the internet-facing `tools`), so a compromised tool layer cannot read or poison the verdict cache, rate limits or run budgets. Redis URLs carry the password, so they are Secret keys, never literals. The public ingress forwards only the API prefixes (`/api/health|auth|profile|validate|...`): `/metrics` and `/ready` stay cluster-internal. Requests/limits are placeholders until `scripts/measure_resources.sh` (P11-T01).
Images are `idea-validator/<service>:unset` in the base; the overlay or the deploy script sets the commit SHA.
Not yet here: the UI Deployment and the `/` ingress route (P1-T03), CronJobs and Jobs (migrate, seed, scouts, ingestion).
