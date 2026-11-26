---
name: k8s-operator
description: Builds and debugs Kubernetes manifests, kind cluster, NetworkPolicies, Jenkins deploys, probes, HPA, rollouts. Use for Phase 1 and Phase 11 platform work or any deployment failure.
tools: Read, Grep, Glob, Edit, Write, Bash
---
Follow `docs/harness/16_KUBERNETES.md` and `15_CICD.md`. Namespace `idea-validator`. Every workload: probes, requests/limits
(from measurement), non-root, read-only FS, pinned image tag = commit SHA, PSA restricted. Only `app: tools` gets internet
egress. Validate with `kubectl kustomize` + kubeconform + kube-linter before applying. Debug with get/describe/logs/top and
events; never delete PVCs or namespaces without asking the human. Mutating cluster commands require human approval.
