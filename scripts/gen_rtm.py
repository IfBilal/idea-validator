import re
src = open("docs/harness/02_REQUIREMENTS.md").read()
rows = []
section = ""
for line in src.splitlines():
    if line.startswith("## ") or line.startswith("### "):
        section = line.strip("# ").strip()
    m = re.match(r"\| ((?:R|FR|M\d+t?|M6i|M10t|DF|NFR)-?[\w-]*) \| (.+?) \|", line)
    if m and m.group(1) not in ("ID",):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rid, req = cells[0], cells[1]
        if rid.startswith("NFR"):
            req = cells[2]; src_ref = "team/derived"
        else:
            src_ref = cells[2] if len(cells) > 2 else ""
        rows.append((rid, req, src_ref))
phase = {"R":"P0/P12","FR-U1":"P2,P7","FR-U2":"P6,P7","FR-U3":"P5,P7","FR-U4":"P5,P7","FR-U5":"P5,P7","FR-U6":"P5,P7","FR-U7":"P5,P7",
 "FR-U8":"P5,P7","FR-U9":"P5,P7","FR-U10":"P5,P7","M1-":"P1,P2,P7","M2-":"P2","M3-":"P5,P6","M4-":"P3","M5-":"P2","M6-":"P3","M6i-":"P3",
 "M7-":"P4","M8-":"P5,P6","M9-":"P5","M10-":"P5,P8","M10t-":"P8","M11-":"P2,P10","M12-":"P5","M13-":"P2,P9","M14-":"P0,P1,P11","DF-":"P0","NFR-":"all"}
spec = {"R":"CLAUDE.md, 15, 23","FR-U":"01 §3, 22","M1-":"05, 22, 13","M2-":"04, 05","M3-":"07","M4-":"10 §4","M5-":"10 §3, 13","M6-":"10 §6-7","M6i-":"10 §5",
 "M7-":"10 §8","M8-":"09 §2","M9-":"09 §5","M10-":"11","M10t-":"11","M11-":"08","M12-":"09 §3, 13","M13-":"12","M14-":"15, 16","DF-":"04, schemas/","NFR-":"13, 14, 16"}
test = {"R":"PR template, gitleaks, contribution check","FR-U":"gateway API tests + UI component tests + E2E","M1-":"services/gateway/tests, services/ui tests",
 "M2-":"services/profile/tests (unit+integration)","M3-":"services/orchestrator/tests (routing, behaviour)","M4-":"services/scouts/tests","M5-":"services/tools/tests (+ netpol_test.sh)",
 "M6-":"services/kb/tests, eval/retrieval","M6i-":"jobs/ingestion/tests (idempotency)","M7-":"services/analyst/tests","M8-":"services/ideation/tests","M9-":"services/validators/tests",
 "M10-":"services/scorer/tests, finetune.compare","M10t-":"jobs/finetune/tests (split)","M11-":"services/verdict/tests + golden vectors","M12-":"services/critic/tests",
 "M13-":"services/agentops/tests, behaviour tests, eval job","M14-":"GHA + Jenkins runs","DF-":"libs/common schema drift test","NFR-":"see 14"}
ev = {"R":"AI log, ADRs, contribution table","FR-U1":"screenshot: profile","FR-U2":"screenshot: Discover results","FR-U3":"screenshot: live run/trace","FR-U4":"screenshot: approval dialog",
 "FR-U5":"demo: clarifying question","FR-U6":"screenshot: verdict card","FR-U7":"screenshot: what-if v2","FR-U8":"screenshot: challenge","FR-U9":"screenshot: compare/history",
 "FR-U10":"screenshot: INSUFFICIENT EVIDENCE","M1-":"screenshots, HPA","M2-":"report, code","M3-":"trace screenshot","M4-":"report: sources/counts","M5-":"NetworkPolicy test screenshot",
 "M6-":"retrieval result screenshot","M6i-":"card counts in report","M7-":"niche map in report","M8-":"Discover trace","M9-":"verdict card evidence links",
 "M10-":"fine-tuning comparison screenshot","M10t-":"repo jobs/finetune","M11-":"report worked example + confusion matrices","M12-":"challenge screenshot, blocked injections panel",
 "M13-":"trace, dashboard, drift screenshots","M14-":"CI/CD, pods, rollout/rollback screenshots","DF-":"schemas/ folder","NFR-":"report"}
def pick(d, rid):
    best = ""
    for k in d:
        if rid.startswith(k) and len(k) > len(best):
            # avoid FR-U1 matching FR-U10
            if k[-1].isdigit() and len(rid) > len(k) and rid[len(k)].isdigit():
                continue
            best = k
    return d.get(best, "")
out = ["# 26 — Requirements Traceability Matrix", "",
 "Generated from `02_REQUIREMENTS.md` (regenerate with `python scripts/gen_rtm.py`). Columns: requirement → spec doc → phase → where it is tested → evidence for the report.",
 "Agents: change **Status** (☐ → 🟡 in progress → ✅ done + PR #) as work completes. A requirement is ✅ only when tested AND evidenced.", "",
 "| ID | Requirement | Source | Spec | Phase | Tested in | Evidence | Status |", "|---|---|---|---|---|---|---|---|"]
for rid, req, s in rows:
    out.append(f"| {rid} | {req} | {s} | {pick(spec,rid)} | {pick(phase,rid)} | {pick(test,rid)} | {pick(ev,rid)} | ☐ |")
out.append(f"\nTotal requirements: **{len(rows)}**.")
open("docs/harness/26_REQUIREMENTS_TRACEABILITY_MATRIX.md","w").write("\n".join(out)+"\n")
print(len(rows))
