# Labelled verdict set (Guide §10): tuning and measuring ONLY. NEVER used to train the scoring model.
File: `labelled_set.jsonl`, one object per line:
```json
{"id": "L001", "idea_text": "...", "profile": {...student_profile...},
 "human_verdict": "GO|PIVOT|NO-GO|INSUFFICIENT_EVIDENCE",
 "category": "clear_go|clear_nogo|borderline|thin_evidence",
 "labels": {"bilal": "GO", "taimur": "PIVOT"}, "resolved_by": "discussion", "notes": "why"}
```
Rules:
- ≥ 40 items: ≥ 10 clear GOs, ≥ 10 clear NO-GOs, ≥ 10 borderline, ≥ 10 thin-evidence (A-16).
- Include the same idea with different profiles (e.g. 200 h team vs 20 h solo) to test profile sensitivity.
- Both members label **independently** first (`labels`), then resolve differences; record who labelled what.
- The fine-tune split script asserts no overlap (id and text similarity ≥ 0.9) with this set.
- Don't tune thresholds so tightly to this set that they fail on new ideas: use k-fold in the tuning script.
