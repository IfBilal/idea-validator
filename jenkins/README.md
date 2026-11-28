# Jenkins (delivery pipeline, P1-T08)

`scripts/jenkins_up.sh` builds this image and starts Jenkins on **http://127.0.0.1:8085** (admin password is generated once
into `~/.config/idea-validator-jenkins/admin-password`, mode 600, never in git). `scripts/jenkins_up.sh stop` removes the container.

What is in it: Jenkins LTS (JDK 21), plugins from `plugins.txt`, docker CLI (pinned sha256 of the static tarball; Docker publishes no checksum file) and kubectl (checked
against dl.k8s.io), make, python3, uv. The Jenkins base image and the uv image are pinned by digest, plugins by version. Configuration as code (`casc.yaml`): one admin user, the `kubeconfig-kind` file credential taken from
a mounted file, and the pipeline job `idea-validator`, which reads the `Jenkinsfile` from the repository mounted at `/repo`
(branch `*/main`; override with `JENKINS_BRANCH='*/some-branch'` to test a branch).

Walk-through for screenshots: `docs/jenkins.md`.

Pipeline: Build (`make images`) -> Push (`scripts/push_images.sh`, proves every image is in the registry at the tag) -> Deploy
(`scripts/deploy.sh`) -> Smoke (`scripts/smoke_test.sh`) -> nightly Eval (`scripts/run_eval.sh`). Every cluster step goes through
`scripts/lib/kube_guard.sh`. On failure `scripts/rollback_changed.sh <tag>` undoes
the Deployments this build changed (image has the build's tag and a previous revision exists).

## Security
- Listens on 127.0.0.1 only. It runs on the host network and mounts `/var/run/docker.sock`, which is root-equivalent on this
  machine: whoever can log in can run commands as root. Explicit local-only risk acceptance with mitigations: `docs/decisions/ADR-012-jenkins-docker-socket.md`.
- The admin password is a mounted file read by casc, `JENKINS_BRANCH` is validated (`^\*/[A-Za-z0-9._/-]+$`).
- The kubeconfig is only usable for the project cluster: `scripts/lib/kube_guard.sh` refuses any context other than
  `kind-idea-validator`, whether it comes from `kind` or from the Jenkins credential (`CI_KUBECONFIG`); it also requires a local
  server (127.0.0.1 / localhost / kind control-plane) and no `exec` / `auth-provider` entries, and never overwrites the project
  kubeconfig on refusal.
- `/repo` is mounted read-only; `hudson.plugins.git.GitSCM.ALLOW_LOCAL_CHECKOUT=true` is set because the source is a local path.

## Known limits
- The Eval stage applies `k8s/base/eval/eval-job.yaml`, which does not exist yet (Dev B, P9-T04); it only runs on the nightly timer.
- `make images` uses the legacy docker builder (no buildx in the Jenkins image): about a minute per service.
- Plugins are pinned at top level (versions from the Jenkins update center for core 2.580.3); their dependencies are still resolved
  by `jenkins-plugin-cli` at image build. Not verified here: no running Jenkins, image build or pipeline run.
