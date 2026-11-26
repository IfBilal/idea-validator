---
description: Security review of the current diff/branch
---
Use the `security-reviewer` subagent on `git diff main...HEAD`. Check every item in `docs/harness/13_SECURITY.md` §12 plus:
prompt-injection paths, verdict integrity (only M11 decides), egress outside M5, secrets, authz scoping, input validation,
container hardening. Output a table: item | PASS/FAIL | file:line | fix. Fix FAIL items before opening/merging the PR.
