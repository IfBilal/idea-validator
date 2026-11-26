# Screenshot checklist (Description §6.3): one-line caption each. Save as PNG here with the listed file name.
| # | Screenshot | File | Phase | Done |
|---|---|---|---|---|
| 1 | Protected main (branch rules) | 01-protected-main.png | P0/P1 | ☐ |
| 2 | A pull request with required checks | 02-pr-required-checks.png | P1 | ☐ |
| 3 | The merge-conflict pull request (conflict + resolution) | 03-merge-conflict-pr.png | P1 | ☐ |
| 4 | Green GitHub Actions run | 04-gha-green.png | P1+ | ☐ |
| 5 | Failing GitHub Actions run | 05-gha-failing.png | P11 | ☐ |
| 6 | Green Jenkins run | 06-jenkins-green.png | P1+ | ☐ |
| 7 | Failing Jenkins run | 07-jenkins-failing.png | P11 | ☐ |
| 8 | All pods running (`kubectl get pods -n idea-validator`) | 08-pods-running.png | P11 (final) | ☐ |
| 9 | A rollout (`kubectl rollout status` + history) | 09-rollout.png | P11 | ☐ |
| 10 | A rollback (`kubectl rollout undo`) | 10-rollback.png | P11 | ☐ |
| 11 | NetworkPolicy test (non-M5 fails, M5 succeeds) | 11-netpol-test.png | P1/P11 | ☐ |
| 12 | UI: profile | 12-ui-profile.png | P7 | ☐ |
| 13 | UI: Discover results | 13-ui-discover.png | P7 | ☐ |
| 14 | UI: verdict card | 14-ui-verdict-card.png | P7 | ☐ |
| 15 | UI: challenge | 15-ui-challenge.png | P7 | ☐ |
| 16 | UI: what-if version 2 | 16-ui-whatif-v2.png | P7 | ☐ |
| 17 | UI: INSUFFICIENT EVIDENCE | 17-ui-insufficient-evidence.png | P7 | ☐ |
| 18 | A trace | 18-trace.png | P9 | ☐ |
| 19 | The dashboard | 19-dashboard.png | P9 | ☐ |
| 20 | A drift report | 20-drift-report.png | P9 | ☐ |
| 21 | The retrieval result | 21-retrieval-result.png | P3 | ☐ |
| 22 | The fine-tuning comparison | 22-finetune-comparison.png | P8 | ☐ |
Extra (nice for report): approval dialog, live run panel, HPA scaling, Grafana cost panel.
Blur any token/secret before committing.
