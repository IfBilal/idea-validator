---
description: Pick, plan and start the next unblocked task from the backlog
---
1. Read `PROGRESS.md` (current phase, completed tasks, blockers) and `docs/harness/20_TASK_BACKLOG.md`.
2. Choose the next task of the current phase whose dependencies are all completed (prefer critical-path tasks, see
   `docs/harness/18_CRITICAL_PATH_AND_PARALLELIZATION.md`; respect the owner column if the human said which developer you are).
3. Open the spec docs the task references. Write a 3–10 line plan into PROGRESS.md → "Current task plan"
   (files to touch, tests to add, acceptance criteria, requirement IDs).
4. Create the branch `<type>/<module>-<short-desc>` and follow the autonomous loop in CLAUDE.md §2.
If nothing is unblocked, write why in "Blocked / Needs human" and propose what the humans should do.
$ARGUMENTS
