# ADR-005: kind + Calico + local registry as the Kubernetes target
- **Status:** Accepted
- **Context:** Guide §4.3/§9.3: the CNI must enforce NetworkPolicy; kind's default kindnet does not.
- **Options:** (1) kind + Calico, (2) k3d (k3s netpol controller), (3) minikube --cni=calico.
- **Decision:** (1) kind with `disableDefaultCNI: true` + Calico, plus a local `registry:2` and ingress-nginx.
- **Why:** reproducible config file in git, multi-node, well-documented Calico enforcement, fast image loading via local registry.
- **Cost:** ~1 GB extra RAM for Calico; setup script needed.
- **AI use:** Drafted with AI; verified by running the NetworkPolicy test.
