#!/usr/bin/env bash
# P1-T04: local registry + kind (no default CNI) + Calico + ingress-nginx + metrics-server, all pinned.
# Idempotent: safe to re-run. Usage: ./scripts/k8s_up.sh   (make k8s-up)
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER=idea-validator
REG_NAME=kind-registry
REG_PORT=5001
NODE_IMAGE=kindest/node:v1.31.4
REGISTRY_IMAGE=registry:2.8.3
CALICO=v3.29.1
INGRESS_NGINX=controller-v1.12.0
METRICS_SERVER=v0.7.2
KIND_CONFIG=${KIND_CONFIG:-k8s/kind-config.yaml}  # override for a 1-node cluster on small hosts

# 3 kind nodes + other containers exhaust the default inotify limit (128) and nodes fail to boot.
inotify=$(sysctl -n fs.inotify.max_user_instances 2>/dev/null || echo 0)
if [ "$inotify" -lt 512 ] && [ "$KIND_CONFIG" = k8s/kind-config.yaml ]; then
  echo "fs.inotify.max_user_instances=$inotify (<512): 3-node kind will not boot." >&2
  echo "Fix once: sudo sysctl -w fs.inotify.max_user_instances=512   (persist in /etc/sysctl.d/), or KIND_CONFIG=<1-node config>." >&2
  exit 1
fi

log() { printf '\n==> %s\n' "$*"; }

log "local registry ${REG_NAME} on localhost:${REG_PORT}"
if [ "$(docker inspect -f '{{.State.Running}}' "$REG_NAME" 2>/dev/null || true)" != "true" ]; then
  docker rm -f "$REG_NAME" >/dev/null 2>&1 || true
  docker run -d --restart=always -p "127.0.0.1:${REG_PORT}:5000" --network bridge --name "$REG_NAME" "$REGISTRY_IMAGE" >/dev/null
fi

log "kind cluster ${CLUSTER}"
if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --config "$KIND_CONFIG" --image "$NODE_IMAGE"
fi

log "wire registry into node containerd (localhost:${REG_PORT} -> ${REG_NAME}:5000)"
for node in $(kind get nodes --name "$CLUSTER"); do
  docker exec "$node" mkdir -p "/etc/containerd/certs.d/localhost:${REG_PORT}"
  printf '[host."http://%s:5000"]\n' "$REG_NAME" |
    docker exec -i "$node" tee "/etc/containerd/certs.d/localhost:${REG_PORT}/hosts.toml" >/dev/null
done
docker network connect kind "$REG_NAME" 2>/dev/null || true

log "Calico ${CALICO} (kindnet does not enforce NetworkPolicy)"
kubectl create -f "https://raw.githubusercontent.com/projectcalico/calico/${CALICO}/manifests/tigera-operator.yaml" 2>/dev/null ||
  kubectl replace -f "https://raw.githubusercontent.com/projectcalico/calico/${CALICO}/manifests/tigera-operator.yaml"
kubectl wait --for=condition=Established crd/installations.operator.tigera.io --timeout=120s
kubectl apply -f "https://raw.githubusercontent.com/projectcalico/calico/${CALICO}/manifests/custom-resources.yaml"
until kubectl get ds/calico-node -n calico-system >/dev/null 2>&1; do sleep 3; done
kubectl rollout status ds/calico-node -n calico-system --timeout=300s
kubectl wait --for=condition=Ready nodes --all --timeout=300s

log "ingress-nginx ${INGRESS_NGINX}"
kubectl apply -f "https://raw.githubusercontent.com/kubernetes/ingress-nginx/${INGRESS_NGINX}/deploy/static/provider/kind/deploy.yaml"
kubectl rollout status deploy/ingress-nginx-controller -n ingress-nginx --timeout=300s

log "metrics-server ${METRICS_SERVER} (--kubelet-insecure-tls for kind)"
kubectl apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/${METRICS_SERVER}/components.yaml"
kubectl patch deploy metrics-server -n kube-system --type=json \
  -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' 2>/dev/null || true
kubectl rollout status deploy/metrics-server -n kube-system --timeout=300s

if [ -f k8s/base/namespace.yaml ]; then
  log "namespace"
  kubectl apply -f k8s/base/namespace.yaml
fi
log "done"
kubectl get nodes
