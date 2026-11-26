---
description: Run the testing gate and security gate for a phase and record the result
argument-hint: <phase number>
---
Run the gates for phase $ARGUMENTS exactly as listed in `docs/harness/19_QUALITY_GATES.md`:
1. Universal testing gate commands, then the phase-specific testing items.
2. Universal security gate commands, then the phase-specific security items. Use the `security-reviewer` subagent
   to review all PRs merged in this phase against `docs/harness/13_SECURITY.md` §12.
3. For every item, record PASS/FAIL with evidence (command output summary, file paths, screenshots to capture).
4. Append a row to PROGRESS.md "Gate log" and tick the phase in the checklist only if BOTH gates pass.
5. On FAIL follow the gate failure protocol (issue, failing test, fix, re-run full gate). Do not start dependent tasks.
6. Remind the humans which screenshots from `docs/screenshots/CHECKLIST.md` belong to this phase.
