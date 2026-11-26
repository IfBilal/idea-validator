# 19 — Quality Gates (Testing Gate + Security Gate after every phase)

Rule: a phase is **complete only when both gates pass**. Run `/phase-gate <N>` (Claude Code) or follow this file manually.
Record the result in `PROGRESS.md` → "Gate log" as:
`| Phase N | Testing PASS/FAIL | Security PASS/FAIL | date | commit SHA | notes / evidence links |`.
A FAIL blocks the next phase's *dependent* tasks. Fix → re-run the full gate (not just the failed item).

## Universal checks (run at every gate)
### Testing gate (universal)
```
make lint typecheck            # ruff + mypy clean
make test                      # all unit/component/behaviour tests green, coverage thresholds met (14 §4)
make test-verdict              # golden vectors (from Phase 2 on)
make test-retrieval            # retrieval gate (from Phase 3 on)
GitHub Actions on the phase's last PR: all required checks green
Jenkins: last main build green and deployed (from Phase 1 on); smoke test green
```
- [ ] No skipped/xfail tests without linked issue. - [ ] New code has tests (diff coverage ≥ 80%).
- [ ] Schema drift test green. - [ ] `PROGRESS.md` + traceability matrix updated for requirements finished in this phase.

### Security gate (universal)
```
gitleaks detect --source . --log-opts="--all"      # no secrets in history
pip-audit ; (cd services/ui && npm audit --audit-level=high)
trivy fs --severity CRITICAL --exit-code 1 .
trivy image --severity CRITICAL --exit-code 1 <every image built this phase>
kubeconform + kube-linter on kustomize build output (from Phase 1)
make netpol-test (from Phase 1): non-M5 egress blocked, M5 allowed
```
- [ ] No secrets in code/config/fixtures/images; `.env` not tracked; `.env.example` has names only.
- [ ] No new egress path except M5. - [ ] All new endpoints authenticated/scoped or internal-token protected.
- [ ] Inputs validated; outputs schema-validated. - [ ] Containers non-root, read-only FS, pinned, no `latest`.
- [ ] Per-PR security checklist (13 §12) ticked on every PR merged in this phase.

## Phase-specific gates
| Phase | Testing gate (in addition to universal) | Security gate (in addition to universal) |
|---|---|---|
| 0 | CI runs on a PR; main protected (try direct push → rejected); schemas importable; compose healthy | gitleaks pre-commit active; `.gitignore` covers `.env`, `*.gguf`, kubeconfig; repo visibility decided |
| 1 | All pods Running/Ready; Jenkins green build→push→deploy→smoke; stub validate through Ingress returns valid card; merge conflict PR done | **NetworkPolicy test passes** (screenshot); PSA restricted label; Secrets only via script; Trivy CRITICAL = 0 on all images; Jenkins credentials not in repo |
| 2 | M11: every 08 §7 test + 24 golden vectors + hypothesis; coverage ≥ 95%; M2 migrations up/down; auth tests; M5 unit tests | Auth: lockout, generic errors, JWT expiry, other-user access → 404; argon2id; rate limit 429; M5: SSRF private IP block, redirect off-list blocked, robots disallow honoured, **sanitiser corpus ≥ 25 cases pass**, LLM proxy budget 402 |
| 3 | ≥ 1,500 cards, ≥ 3 sources (`/stats`); ingestion idempotent (run twice); every chunk has card_id (test); retrieval gate required & green; baseline committed | Data-source terms/robots documented; no private data fields (schema test: no author/email fields); keys only in Secrets; scouts have no egress except via M5 (netpol test re-run) |
| 4 | ≥ 15 named niches with status; gaps evidence ids valid; features deterministic tests | LLM niche naming (if used) goes through M5; cluster job runs non-root |
| 5 | Validate end-to-end on 3 ideas; U4/U5/U6/U7/U8/U10 API tests; behaviour tests (invalid JSON, tool timeout, poisoned page, approval decline, cache hit); 4 scorers verified parallel in trace | **No model decides the verdict** (code review by `verdict-guardian` + test asserting verdict == M11 output); input rail on all user text; poisoned page doesn't change score; claim check rejects missing ids & non-KB competitors; approval required above budget |
| 6 | Discover ranked list with verdict + reason; ≤ 3 rounds asserted; dropped ideas traced; profile change changes output | Debate loop bounded (steps/cost caps tested); ideator outputs cite evidence (validator); no internet from ideation pod |
| 7 | Component tests for every component in 14 §2 required in CI; U1–U10 demo checklist in browser | No `dangerouslySetInnerHTML`; CSP header; tokens not in localStorage if avoidable (memory + refresh) or documented; only gateway called from browser; API base from runtime config |
| 8 | ≥ 300 examples, ≥ 60 hand-checked marked; grouped split test; no overlap with labelled set test; comparison JSON with all 6 metrics; fallback path test | Weights not in git (`git ls-files | grep -E 'gguf|safetensors'` empty); model download via M5 + sha256 verified; scorer container non-root |
| 9 | Replay reproduces a stored verdict; eval Job writes confusion matrix; drift report generated; dashboard 6 panels show data; empty-source behaviour test | Traces redact secrets/PII (test); dashboard not exposed publicly without auth (port-forward or auth); blocked-injection metric visible |
| 10 | Before/after matrices; agreement and false-GO reported; tuned values via ConfigMap PR; golden vectors still pass (with old config) and new config tests added | Labelled set never used in training (assert); tuning results reproducible (seeded) |
| 11 | Requests/limits from measurement CSV; HPA scales; rollout + rollback done; E2E smoke green; fresh-clone start works | Final Trivy/gitleaks/audit clean; kube-linter clean; optional ZAP baseline no high findings |
| 12 | Report numbers match `eval/results/*`; README commands verified | No secrets in report/screenshots (blur tokens); contribution table matches git history |

## Gate failure protocol
1. Record FAIL + cause in PROGRESS.md. 2. Open issue `gate(P<N>): <cause>` label `blocker`. 3. Fix on a branch with a test
that reproduces the failure. 4. Re-run the whole gate. 5. After 3 failed attempts on the same cause → stop and ask the
humans (write in "Blocked / Needs human").
