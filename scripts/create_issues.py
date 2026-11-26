"""Create one GitHub issue per backlog task (docs/harness/20_TASK_BACKLOG.md). Requires `gh auth login`.
Usage: python scripts/create_issues.py [--dry-run]"""
import re, subprocess, sys
from pathlib import Path
dry = "--dry-run" in sys.argv
text = Path("docs/harness/20_TASK_BACKLOG.md").read_text()
for line in text.splitlines():
    m = re.match(r"\| (P\d+-T\d+) \| (.+?) \| (.+?) \| (.+?) \| (.+?) \| (.+?) \|$", line)
    if not m:
        continue
    tid, task, module, owner, deps, acc = (g.strip() for g in m.groups())
    phase = "phase-" + tid.split("-")[0][1:]
    title = f"[{tid}] {task}"[:250]
    body = (f"**Task:** {tid}\n**Module:** {module}\n**Owner:** {owner}\n**Depends on:** {deps}\n\n"
            f"**Acceptance:** {acc}\n\nSpec: docs/harness/17_IMPLEMENTATION_PLAN.md, docs/harness/20_TASK_BACKLOG.md")
    labels = [phase, f"owner-{owner.replace('+', '')}"] + [m_.lower() for m_ in re.findall(r"M\d+t?i?", module)]
    cmd = ["gh", "issue", "create", "--title", title, "--body", body]
    for lb in labels:
        cmd += ["--label", lb]
    print(" ".join(cmd[:4]) if not dry else title)
    if not dry:
        subprocess.run(["gh", "label", "create", *labels[:1], "--force"], check=False, capture_output=True)
        for lb in labels:
            subprocess.run(["gh", "label", "create", lb, "--force"], check=False, capture_output=True)
        subprocess.run(cmd, check=True)
