# Labelled verdict set (Guide §10): tuning and measuring ONLY. NEVER used to train the scoring model.
File: `labelled_set.jsonl`, one object per line:
```json
{"id": "L001", "idea_text": "...", "profile": {...student_profile...},
 "human_verdict": "GO|PIVOT|NO-GO|INSUFFICIENT_EVIDENCE",
 "category": "clear_go|clear_nogo|borderline|thin_evidence",
 "labels": {"bilal": "GO", "taimoor": "PIVOT"}, "resolved_by": "discussion", "notes": "why"}
```
Rules:
- ≥ 40 items: ≥ 10 clear GOs, ≥ 10 clear NO-GOs, ≥ 10 borderline, ≥ 10 thin-evidence (A-16).
- Include the same idea with different profiles (e.g. 200 h team vs 20 h solo) to test profile sensitivity.
- Both members label **independently** first (`labels`), then resolve differences; record who labelled what.
- The fine-tune split script asserts no overlap (id and text similarity ≥ 0.9) with this set.
- Don't tune thresholds so tightly to this set that they fail on new ideas: use k-fold in the tuning script.

## Workflow
1. `candidates.jsonl` (committed) holds 43 drafted ideas with student profiles and an `intended_category` hint
   (11 clear_go, 12 clear_nogo, 10 borderline, 10 thin_evidence; five same-idea-different-profile pairs share an `idea_group`).
   The hint is only what we expect: **the real verdict depends on the evidence in the knowledge base**, so a drafted
   "clear GO" may turn out thin-evidence and that is a valid finding.
2. After ingestion (Phase 3) run each candidate through Validate with the cache off, so the evidence is the real KB.
3. Bilal and Taimoor each label independently (no peeking), writing `{"bilal": "...", "taimoor": "..."}` into `labels`.
4. Resolve differences by discussion, fill `human_verdict`, `category`, `resolved_by` and `notes`, and save the result as
   `labelled_set.jsonl`. Never edit `candidates.jsonl` after labelling starts (add new ideas as new ids).
5. Keep at least 10 per category in the final set; add candidates if a category ends up short.
