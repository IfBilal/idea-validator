# Jenkins walk-through (screenshots 6, 7, 9, 10)

Reference: `jenkins/README.md`, `docs/decisions/ADR-012-jenkins-docker-socket.md`, `docs/harness/15_CICD.md`.

## 1. Start it
1. `make k8s-up` (kind cluster `idea-validator`, registry on `localhost:5001`), then `make deploy TAG=<sha>` once so Deployments exist.
2. `scripts/jenkins_up.sh` (override the built branch with `JENKINS_BRANCH='*/some-branch'`; it must match `*/<letters digits . _ - />`).
   It refuses to start unless the kubeconfig is the `kind-idea-validator` cluster.
3. Open http://127.0.0.1:8085, user `admin`, password in `~/.config/idea-validator-jenkins/admin-password`.
   Stop with `scripts/jenkins_up.sh stop`.

## 2. Credentials a human adds
The `kubeconfig-kind` file credential is created by configuration as code from the mounted kubeconfig, so nothing is needed
for the pipeline itself. Add nothing to git. If you rotate the cluster, run `scripts/jenkins_up.sh` again. Other secrets
(registry, LLM keys) are not used by the pipeline; if added, use Jenkins credentials only.

## 3. Green run (screenshots 6 and 7)
1. Merge or commit to the built branch; SCM polling picks it up within 2 minutes, or press **Build Now** on `idea-validator`.
2. Stages: Checkout, Build, Push, Deploy, Smoke (Eval only on the nightly timer). Screenshot the stage view (6) and the console
   tail showing `all images are in localhost:5001 at <tag>` and the smoke test passing (7).

## 4. Failing run with rollback (screenshots 9 and 10)
1. On a throwaway branch break the smoke test on purpose, for example make `gateway` crash at startup or point a probe at a wrong
   path, push it, and build that branch (`JENKINS_BRANCH='*/broken-demo' scripts/jenkins_up.sh`).
2. Deploy rolls the changed Deployments to the new tag, Smoke fails, and the `post { failure }` block runs
   `scripts/rollback_changed.sh <tag>`: it undoes only Deployments running that tag that have an earlier revision.
   Screenshot the red stage view (9) and the console lines `rollback <name> (...)` plus `rolled back: ...` (10);
   `kubectl -n idea-validator rollout history deploy/gateway` shows the extra revision.
3. If the rollback itself fails the build is marked FAILURE with that stage red; it is never swallowed.
4. Delete the broken branch afterwards and re-run on `main` to get back to green.
