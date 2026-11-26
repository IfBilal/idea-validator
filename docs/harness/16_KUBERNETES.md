# 16 — Kubernetes

Reference: `guide_fig3_k8s_from_pdf.png`. Namespace **`idea-validator`**. Only M5 has a route to the internet.

## 1. Local cluster (Guide §4.3, §9.3)
`scripts/k8s_up.sh` (`make k8s-up`):
1. Start local registry container `kind-registry` on port 5001 (host) / 5000 (in cluster network).
2. `kind create cluster --config k8s/kind-config.yaml` with `networking.disableDefaultCNI: true`, `podSubnet: 192.168.0.0/16`,
   containerd registry mirror for `localhost:5001` → `kind-registry:5000`, 1 control-plane + 2 workers, extraPortMappings 80/443.
3. Install **Calico** (operator manifests, pinned version) → wait for `calico-node` Ready. (kindnet does not enforce NetworkPolicy.)
4. Install ingress-nginx (kind provider manifest, pinned), metrics-server (with `--kubelet-insecure-tls` for kind).
5. `kubectl apply -f k8s/base/namespace.yaml` (with PSA labels).
Alternative (ADR if used): k3d (k3s ships a NetworkPolicy controller) or minikube `--cni=calico`.

## 2. Manifests layout (Kustomize)
```
k8s/
  kind-config.yaml
  base/
    kustomization.yaml  namespace.yaml
    configmaps/  (generated: verdict-config from config/verdict.yaml, budgets-config, sources-config, rubrics (files), prompts)
    secrets/README.md (created by scripts/create_secrets.sh, never committed)
    postgres/ statefulset.yaml service.yaml pvc via volumeClaimTemplates
    redis/ statefulset.yaml service.yaml
    qdrant/ statefulset.yaml service.yaml
    ui/ gateway/(+hpa.yaml) profile/ orchestrator/ tools/ kb/ analyst/ ideation/ validators/ scorer/(+pvc models) verdict/ critic/ agentops/(+pvc traces)
    scouts/ deployment.yaml (on-demand) cronjob-hn.yaml cronjob-github.yaml cronjob-reddit.yaml cronjob-ph.yaml
    jobs/ cronjob-ingestion.yaml cronjob-drift.yaml cronjob-niches.yaml job-migrate.yaml job-seed.yaml job-model-import.yaml
    eval/ eval-job.yaml
    netpol/ default-deny.yaml allow-dns.yaml allow-intra.yaml allow-tools-egress.yaml allow-ingress-to-ui-gateway.yaml
    ingress.yaml
  overlays/dev/kustomization.yaml (image tags, replicas, resources)
  observability/ prometheus.yaml grafana.yaml dashboard.json
```

## 3. Objects to deploy (Guide §9.1) (checklist)
| Object | For |
|---|---|
| Deployments + Services | M1 (ui, gateway), M2 (profile), M3, M5, M6, M7, M8, M9, M10, M11, M12, M13 |
| StatefulSet / PVC | Postgres, Redis (if persistent), vector store (Qdrant); PVCs for traces, models, eval results |
| CronJobs | scouts (per source, hourly/6-hourly/daily), ingestion + freshness check, niche recompute, drift report |
| Jobs | evaluation runs, DB migrate, seed from snapshot, fine-tuned model import |
| ConfigMaps | weights, thresholds, floors (verdict-config), rubric versions + rubric files, budgets, sources/allow-list, prompts |
| Secrets | API keys (LLM, GitHub, Reddit, PH), DB password, JWT secret, internal token |
| Probes | liveness `/health`, readiness `/ready` on **every** service (startupProbe for scorer: model load) |
| Requests/limits | on every container, **from measurements** (`kubectl top`) |
| Autoscaler | HPA on gateway (min 2, max 5, CPU 70%) |
| NetworkPolicy | default deny; only M5 internet; ui → gateway only |
| Rollout | RollingUpdate `maxUnavailable: 0`, `maxSurge: 1`; `kubectl rollout undo` demonstrated |
| Ingress | single entry point: `/` → ui, `/api` → gateway (rewrite) |

## 4. Standard Deployment template (every service)
```yaml
apiVersion: apps/v1
kind: Deployment
metadata: { name: verdict, labels: { app: verdict, module: m11 } }
spec:
  replicas: 2
  strategy: { type: RollingUpdate, rollingUpdate: { maxUnavailable: 0, maxSurge: 1 } }
  selector: { matchLabels: { app: verdict } }
  template:
    metadata: { labels: { app: verdict, module: m11 } }
    spec:
      automountServiceAccountToken: false
      securityContext: { runAsNonRoot: true, runAsUser: 10001, fsGroup: 10001, seccompProfile: { type: RuntimeDefault } }
      containers:
      - name: verdict
        image: registry/verdict:abc1234          # set by kustomize/Jenkins, never :latest
        ports: [{ containerPort: 8000 }]
        envFrom: [{ configMapRef: { name: verdict-config } }]
        readinessProbe: { httpGet: { path: /ready, port: 8000 }, periodSeconds: 5 }
        livenessProbe:  { httpGet: { path: /health, port: 8000 }, periodSeconds: 10 }
        resources: { requests: { cpu: 100m, memory: 128Mi }, limits: { cpu: 500m, memory: 256Mi } }   # replace with measured
        securityContext: { allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: ["ALL"] } }
        volumeMounts: [{ name: tmp, mountPath: /tmp }]
      volumes: [{ name: tmp, emptyDir: {} }]
```

## 5. NetworkPolicy design
| Policy | Selector | Rule |
|---|---|---|
| `default-deny` | all pods | `policyTypes: [Ingress, Egress]`, no rules |
| `allow-dns` | all pods | egress to `kube-system` k8s-app=kube-dns UDP/TCP 53 |
| `allow-ingress-controller` | `app in (ui, gateway)` | ingress from ingress-nginx namespace |
| `ui-to-gateway-only` | `app: ui` | egress only to `app: gateway` (+ DNS) |
| `allow-intra` | per service | ingress only from its callers (edge table in 03 §1): e.g. verdict ← orchestrator; scorer ← validators; kb ← orchestrator, analyst, ideation, validators, critic, ingestion; tools ← scouts, ideation, validators, critic, orchestrator, scorer, model-import, intake; postgres ← profile, kb, ingestion, agentops, orchestrator(checkpointer); redis ← gateway, profile, orchestrator, tools, kb; qdrant ← kb, ingestion; agentops ← all (trace) |
| `tools-internet-egress` | `app: tools` | egress `0.0.0.0/0` except RFC1918/link-local, ports 443 (and 80 for robots.txt if needed) |
Test (`scripts/netpol_test.sh`, screenshot): `kubectl exec deploy/kb -- python -c "import httpx;httpx.get('https://api.github.com',timeout=5)"` → **fails**;
`kubectl exec deploy/tools -- …` → **200**. Also: `kubectl exec deploy/ui -- wget -T3 verdict:8000/health` → fails.

## 6. Measuring requests & limits (required, report section)
`scripts/measure_resources.sh`: run load (`hey`/`k6` or a Python script: 20 concurrent validates in replay mode + KB
searches) for 5 minutes; sample `kubectl top pods -n idea-validator --containers` every 15 s into CSV; set
`requests ≈ p50 usage × 1.2`, `limits ≈ peak × 1.5` (memory limit ≥ peak × 1.3 to avoid OOM). Commit CSV + resulting
values table (report: "how you measured requests and limits").

## 7. Commands (Guide §9.2)
```
kubectl get pods -n idea-validator               kubectl logs deploy/verdict -n idea-validator
kubectl describe pod <name> -n idea-validator    kubectl top pods -n idea-validator
kubectl rollout status deploy/gateway -n idea-validator
kubectl rollout undo   deploy/gateway -n idea-validator
```

## 8. Startup order / jobs
postgres, redis, qdrant Ready → `job-migrate` (Alembic) → `job-seed` (restore snapshot into Postgres + Qdrant) →
`job-model-import` → app deployments (readiness waits for deps) → CronJobs active.
