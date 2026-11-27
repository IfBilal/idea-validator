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

What is enforced (and tested in `libs/common/tests/test_k8s_manifests.py`): non-root UID 10001, read-only root filesystem, all
capabilities dropped, seccomp RuntimeDefault, no service-account token, probes on `/health` and `/ready`, rolling update with
`maxUnavailable: 0`, no `:latest`, no Secret in git, one NetworkPolicy per workload derived from the call graph (a service accepts
traffic only from its callers and may reach only its callees; every service may post traces to `agentops`), and only `tools` has
internet egress (TCP 443, private ranges excluded).

Secrets are never committed: `app-secrets` (keys `INTERNAL_TOKEN`, `JWT_SECRET`, `DATABASE_URL`, `GROQ_API_KEY`,
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`) is created by `scripts/create_secrets.sh` (P1-T06) from the git-ignored `.env`.
Each service receives only the keys it uses. Requests/limits are placeholders until `scripts/measure_resources.sh` (P11-T01).
Images are `idea-validator/<service>:unset` in the base; the overlay or the deploy script sets the commit SHA.
Not yet here: the UI Deployment and the `/` ingress route (P1-T03), CronJobs and Jobs (migrate, seed, scouts, ingestion).
