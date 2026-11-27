# Screenshots checklist (Description 6.3)

Save PNGs in `docs/screenshots/` with the file name given. Blur tokens, secrets and emails before committing. Fill the Status column when the file exists (a one-line caption goes in the report appendix). Items 1-22 mirror `docs/screenshots/CHECKLIST.md`.

| # | File | Steps / command | Expected content | Status |
|---|---|---|---|---|
| 1 | 01-protected-main.png | GitHub, Settings, Branches (or Rules), the rule for `main` | Require PR, 1 approval, required status checks (`lint-type`, `unit-tests`, `integration-tests`, `contracts`, `docker`, `manifests`, `security`), no direct push | |
| 2 | 02-pr-required-checks.png | Open any PR, checks box at the bottom | All required checks listed, green, "1 approving review" visible | |
| 3 | 03-merge-conflict-pr.png | P1-T12: both members edit the same line on two branches, open PR b; GitHub shows "This branch has conflicts"; screenshot that, then resolve (`git merge origin/main`, fix markers, push) and screenshot the resolved PR | Conflict banner and the resolved state | |
| 4 | 04-gha-green.png | Actions tab, a green `pr-checks` run | All jobs green | |
| 5 | 05-gha-failing.png | P11-T04: push a branch with a deliberate failing test (do not merge), open the run | Red job with the failing step log; then close the PR | |
| 6 | 06-jenkins-green.png | Jenkins job page, a build with stages Checkout, Build, Push, Deploy, Smoke green (needs the Jenkinsfile script gaps fixed first) | Green stage view | |
| 7 | 07-jenkins-failing.png | Jenkins, a build that fails (e.g. a failing smoke) and shows the rollback step | Red stage view and "nothing promoted" message | |
| 8 | 08-pods-running.png | `export KUBECONFIG=~/.kube/idea-validator.kubeconfig; kubectl get pods -n idea-validator -o wide` | All pods `Running`, `READY` full, low restarts | |
| 9 | 09-rollout.png | `kubectl set image deploy/gateway ... :<new-sha> -n idea-validator`, `kubectl rollout status deploy/gateway -n idea-validator`, `kubectl rollout history deploy/gateway -n idea-validator` (P11-T03) | "successfully rolled out" and a history with 2+ revisions | |
| 10 | 10-rollback.png | Deploy a bad image, watch `rollout status` fail, then `kubectl rollout undo deploy/gateway -n idea-validator` | Failure, then undo, then healthy again | |
| 11 | 11-netpol-test.png | `make netpol-test` | Non-M5 pod egress blocked, M5 egress allowed, negative control passes | |
| 12 | 12-ui-profile.png | UI, Profile page, saved profile | Saved profile visible | |
| 13 | 13-ui-discover.png | UI, Discover with a niche | Ranked list, each idea with verdict and short reason | |
| 14 | 14-ui-verdict-card.png | UI, Validate an idea, open the card | Verdict, gates, six scores with evidence links, weights, confidence, stability | |
| 15 | 15-ui-challenge.png | UI, press Challenge on a claim | Critic defends or removes it; score/confidence change shown | |
| 16 | 16-ui-whatif-v2.png | UI, "What would make this GO", apply a change | Version 2 with the engine's new verdict, version list | |
| 17 | 17-ui-insufficient-evidence.png | UI, validate an idea with thin evidence | Card says what was missing, no guessed score | |
| 18 | 18-trace.png | UI trace viewer or `GET /runs/{id}/trace` for a real run (P9-T01, P7-T09) | Tree of agent, tool, model and decision steps under one `run_id` | |
| 19 | 19-dashboard.png | Grafana or `/dashboard` (P9-T06) | Cost per verdict, latency per agent, cache hit rate, fallbacks, blocked injections | |
| 20 | 20-drift-report.png | Output of the drift job (P9-T05) | Freshness, coverage, verdict-distribution shift | |
| 21 | 21-retrieval-result.png | `make test-retrieval` and `eval/results/retrieval-<date>.json` | 4-mode table of Recall@5/10, MRR, nDCG | |
| 22 | 22-finetune-comparison.png | Comparison output `eval/results/finetune_comparison.json` | base vs fine-tuned vs hosted table | |
| 23 (extra) | 23-approval-dialog.png | UI, trigger a deep search over budget | Cost stated, waits for approve/decline | |
| 24 (extra) | 24-live-run-panel.png | UI, watch a run | Running agent, tool calls, spend live | |
| 25 (extra) | 25-hpa-scaling.png | `kubectl get hpa -n idea-validator -w` during the load script (P11-T02) | Replicas rising under load | |
| 26 (extra) | 26-grafana-cost.png | Grafana cost panel | Cost per verdict over time | |
