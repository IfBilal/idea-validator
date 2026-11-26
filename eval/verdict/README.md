# Verdict engine test vectors
- `reference_engine.py`: executable spec of Description §5 / Figure 4 (pure Python). Run `python -I eval/verdict/reference_engine.py`
  to print all cases and regenerate `test_vectors.json`.
- `test_vectors.json`: 24 golden vectors incl. the PDF worked example (70.5 GO BORDERLINE → competition 3.0 → 73.5 stable GO),
  every gate, every threshold edge, floor rules, challenge caps, sensitivity flips and what-if.
- The production engine `services/verdict` must reproduce `expected.verdict`, `stability`, `score`, `confidence`, `gates`,
  `weakest_dimension`, `sensitivity.runs/flipped` and what-if candidates for every vector (CI test).
- If the team changes a rule interpretation: change the reference engine + ADR + regenerate vectors in the same PR.
