# 23 — Deliverables, Report and Screenshots (Description §6)

Submit **through GCR**. Every item below is graded; tick them in `PROGRESS.md` before submitting.

## 1. GitHub repository link
- [ ] Code for every module, **Dockerfiles** (one per module), **workflows** (`.github/workflows/`), **Jenkinsfile**,
      **`k8s/`**, **fine-tuning data and scripts** (`jobs/finetune/`), **rubrics** (`rubrics/`), **evaluation sets** (`eval/`),
      and a **README that lets someone else start the system**.
- [ ] **Markers can open it** (public repo, or add the instructor as collaborator, verified from a logged-out/other account).

## 2. Report (PDF, ≤ 10 pages, not counting screenshots): outline in `docs/report/REPORT_OUTLINE.md`
Required sections (exact list from the Description):
1. **Summary and changes from the architecture above** (e.g. LLM proxy in M5, Learning scored by the feasibility scorer, M2 separate service).
2. **Modules and architecture figure.**
3. **Verdict design**: gates, weights, thresholds, rubrics, **tuning results with a before and after confusion matrix**, **one worked example**.
4. **Knowledge base**: sources, card counts, retrieval results.
5. **Fine-tuning**: data, settings, **comparison table**, **one case where it did not help**.
6. **AgenticOps results.**
7. **Platform**: pipeline, Kubernetes design, **how you measured requests and limits**.
8. **Known limits and next steps.**

## 3. Screenshots with one-line captions (`docs/screenshots/CHECKLIST.md`)
- [ ] Protected main **and** a pull request with required checks
- [ ] The merge-conflict pull request
- [ ] A **green** and a **failing** GitHub Actions run
- [ ] A **green** and a **failing** Jenkins run
- [ ] All pods running
- [ ] A rollout **and** a rollback
- [ ] The NetworkPolicy test
- [ ] The interface: **profile**, **Discover results**, **verdict card**, **challenge**, **what-if version 2**, **INSUFFICIENT EVIDENCE**
- [ ] A **trace**, the **dashboard** and a **drift report**
- [ ] The **retrieval result** and the **fine-tuning comparison**

## 4. AI-usage log + ≥ 3 short design decision records
- [ ] `docs/ai-usage-log.md` (Date | Tool | What you asked for | What you changed | Why), complete for the whole project.
- [ ] `docs/decisions/` ≥ 3 ADRs (≤ 1 page each, Guide §11.1 template: Title, Status, Context, Options, Decision, Why, Cost, AI use).

## 5. Contribution table, signed by both members (Guide §11.2)
- [ ] Modules owned, PRs authored/reviewed, approximate hours, signatures, names + roll numbers.
- [ ] **Must match git history** (`git shortlog -sne`, `gh pr list --state merged --author X`, review counts).

## 6. Final pre-submission checklist
- [ ] Fresh clone → README commands → system up (tested by the member who didn't write the README).
- [ ] All gates in PROGRESS.md PASS. - [ ] No secrets in repo history (gitleaks). - [ ] Report numbers = `eval/results/*`.
- [ ] Both members can explain the whole system (do a 30-min mutual walkthrough using `03_ARCHITECTURE.md`).
