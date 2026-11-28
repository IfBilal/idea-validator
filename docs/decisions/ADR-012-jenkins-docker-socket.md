# ADR-012: Jenkins in Docker with the host socket and host network (local-only risk acceptance)

- **Title:** Run Jenkins as a local container with `/var/run/docker.sock` and `--network host`.
- **Status:** Accepted
- **Context:** The pipeline (Guide §8.2) must build and push images and deploy to the kind cluster on one laptop. The Jenkins
  container therefore needs a Docker daemon (images are built and pushed by the host daemon) and a route to the kind API server
  (`https://127.0.0.1:<random port>`), the ingress and the registry (`localhost:5001`). A mounted Docker socket is root-equivalent
  on the host, and `--network host` removes network isolation. Anyone who can log in to Jenkins, or run a malicious Jenkinsfile
  on a branch it builds, can run commands as root on this machine.
- **Options:** (1) socket + host network, local only. (2) bridge network + `--add-host=host.docker.internal:host-gateway`, with the
  kubeconfig rewritten to that name and `tls-server-name: localhost` (the kind certificate has no such SAN); the smoke test and
  registry check would also need their URLs changed. Not testable here without a running Jenkins, so not adopted. (3) a
  docker-socket-proxy (limits API verbs): the build needs build/push/images, which is most of the dangerous surface anyway.
  (4) rootless Docker/podman for Jenkins only: different daemon from the one `make images` uses.
- **Decision:** Option 1, accepted explicitly as a **local-only** risk for a student project on a personal machine. Never run this
  on a shared host, a server, or anything reachable from a network.
- **Why / mitigations (all enforced by code and tests, `libs/common/tests/test_delivery_scripts.py`):**
  - Jenkins listens on `127.0.0.1` only (`--httpListenAddress=127.0.0.1`), no signup, no anonymous read.
  - The admin password is generated once, mode 600, mounted as a file (`/run/secrets/admin-password`), read by casc with
    `readFile`; it is not in the environment or `docker inspect`.
  - `JENKINS_BRANCH` is validated against `^\*/[A-Za-z0-9._/-]+$` (no `..`) before it reaches the casc job definition.
  - The repository is mounted read-only; plugins, base images and the docker CLI tarball are pinned (version / digest / sha256).
  - Cluster access goes only through `scripts/lib/kube_guard.sh`: the kubeconfig is validated in a temp file (context must be
    `kind-idea-validator`, servers must be `127.0.0.1` / `localhost` / the kind control-plane name, no `exec` / `auth-provider`
    credential plugins) and never replaces the project kubeconfig on refusal. The Jenkinsfile never runs raw `kubectl`.
- **Cost:** Residual root-equivalent exposure while the container runs; stop it with `scripts/jenkins_up.sh stop` when unused.
  Revisit (option 2 or 3) before any use beyond the demo machine.
- **AI use:** Drafted by AI from the PR review; options and mitigations to be confirmed by the team.
