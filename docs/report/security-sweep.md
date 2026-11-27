# Security sweep (P11-T05), first pass

Run on 2026-11-27 against `main` (locked dependencies, 131 third-party packages). Repeat before the
final submission; the CI jobs below run on every PR and are the continuous evidence.

| Check | Where | Result |
|---|---|---|
| Dependency vulnerabilities (`pip-audit` over `uv export --all-packages`) | local, this sweep | 1 finding, accepted (below) |
| Container and filesystem scan (Trivy: vuln, secret, misconfig; fails on CRITICAL) | every PR (`pr-checks.yml`) | green on every merged PR; an earlier CRITICAL in the base image was fixed by pinning `python:3.12.16-slim-bookworm` (#21) |
| Secrets in the working tree and in history (gitleaks) | pre-commit and CI | green on every merged PR; not re-run over full history in this pass (tool not installed locally) |
| Hand-written adversarial review of the fetch pipeline, gateway, critic and orchestrator | PR reviews #17, #22, #38, #41, #46 | findings fixed before merge (SSRF address pinning, ReDoS, compression bombs, ownership 404s, stream caps, fail-closed 503s, id scoping) |
| UI dependencies (`npm audit`) | not applicable yet | UI not merged |

## Accepted finding

`langgraph-sdk 0.3.15`, PYSEC-2026-4240: "LangGraph SDK custom auth silently ignores `actions=` on
resource decorators". It is a transitive dependency of `langgraph==1.0.10`, which requires
`langgraph-sdk<0.4.0`, so the fixed release (0.4.4) cannot be installed without upgrading
LangGraph itself.

Why this is accepted for now:
- The advisory concerns custom authorisation for a **LangGraph Server** deployment. We do not run
  LangGraph Server and nothing in the repository imports `langgraph_sdk`; M3 uses the in-process
  `StateGraph` with a Postgres checkpointer.
- Authentication and ownership checks are our own (gateway JWT, per-user run ownership in M2).

Revisit: upgrade LangGraph (and re-run the orchestrator suite and the behaviour suite) when a release
that allows `langgraph-sdk>=0.4.4` is pinned, then drop this section. CI runs `pip-audit` with
`|| true` (P2 note in `pr-checks.yml`); make it blocking after that upgrade.
