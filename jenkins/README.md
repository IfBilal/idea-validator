# Jenkins (delivery pipeline, P1-T08)

`scripts/jenkins_up.sh` builds this image and starts Jenkins on **http://127.0.0.1:8085** (admin password is generated once
into `~/.config/idea-validator-jenkins/admin-password`, mode 600, never in git). `scripts/jenkins_up.sh stop` removes the container.

What is in it: Jenkins LTS (JDK 21), plugins from `plugins.txt`, docker CLI and kubectl (each checked against the publisher's
checksum), make, python3, uv. Configuration as code (`casc.yaml`): one admin user, the `kubeconfig-kind` file credential taken from
a mounted file, and the pipeline job `idea-validator`, which reads the `Jenkinsfile` from the repository mounted at `/repo`
(branch `*/main`; override with `JENKINS_BRANCH='*/some-branch'` to test a branch).

Pipeline: Build (`make images`) -> Push (`scripts/push_images.sh`, proves every image is in the registry at the tag) -> Deploy
(`scripts/deploy.sh`) -> Smoke (`scripts/smoke_test.sh`) -> nightly Eval. On failure `scripts/rollback_changed.sh <tag>` undoes
the Deployments this build changed (image has the build's tag and a previous revision exists).

## Security
- Listens on 127.0.0.1 only. It runs on the host network and mounts `/var/run/docker.sock`, which is root-equivalent on this
  machine: whoever can log in can run commands as root. Keep it local; a Docker socket proxy would be the next hardening step.
- The kubeconfig is only usable for the project cluster: `scripts/lib/kube_guard.sh` refuses any context other than
  `kind-idea-validator`, whether it comes from `kind` or from the Jenkins credential (`CI_KUBECONFIG`).
- `/repo` is mounted read-only; `hudson.plugins.git.GitSCM.ALLOW_LOCAL_CHECKOUT=true` is set because the source is a local path.

## Known limits
- The Eval stage applies `k8s/base/eval/eval-job.yaml`, which does not exist yet (Dev B, P9-T04); it only runs on the nightly timer.
- `make images` uses the legacy docker builder (no buildx in the Jenkins image): about a minute per service.
- Plugins are installed at their latest compatible version; pin them from `jenkins-plugin-cli --list` before the final release.
