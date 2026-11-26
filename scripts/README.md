# scripts/
| Script | Purpose | Phase |
|---|---|---|
| gen_rtm.py | Regenerate the traceability matrix from 02_REQUIREMENTS.md | ready |
| create_issues.py | Create GitHub issues from the backlog (`--dry-run` first) | ready |
| netpol_test.sh | NetworkPolicy test (non-M5 blocked, M5 allowed) | ready (needs cluster) |
| k8s_up.sh | kind + Calico + registry + ingress + metrics-server | P1 |
| create_secrets.sh | K8s Secrets from local .env | P1 |
| deploy.sh | kustomize edit set image + apply + rollout status | P1 |
| push_images.sh | push all module images at TAG | P1 |
| smoke_test.sh | health of all services + a validate run through Ingress | P1 |
| rollback_changed.sh | `kubectl rollout undo` for deployments changed in a failed build | P1 |
| check_registry.py | prompt/rubric change ⇒ version bump + eval result | P9 |
| check_eval.sh | compare eval results with baseline | P9 |
| measure_resources.sh | load + `kubectl top` sampling → requests/limits CSV | P11 |
