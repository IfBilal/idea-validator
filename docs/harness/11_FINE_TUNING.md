# 11 — Fine-tuning (M10t) and Scoring Model Serving (M10)

**Goal (Guide §6):** not a smarter model than a hosted one, but a **cheaper, faster and more consistent** model that
follows our rubric format and evidence rule.

## 1. Dataset (`jobs/finetune/data/`)
1. **Collect ≥ 300 (idea, evidence, dimension) examples** from the KB covering **all six dimensions** and **all score
   levels 0–5** (target 360 = 6 dims × 6 levels × 10). Ideas: product cards + gap-derived ideas + paraphrased user-style
   ideas. Evidence: top-k retrieval for each idea (same retrieval code as production).
   Script: `python -m finetune.build_candidates --n 400 --out data/candidates.jsonl`.
2. **Draft labels with a hosted model** using the rubric (`python -m finetune.draft_labels`, runs **through M5** if run
   in-cluster, or locally with the team's key outside the cluster). Stratify so every (dimension, level) cell has ≥ 8.
3. **Hand-check ≥ 60** (target 72 = 12 per dimension): both members review (`python -m finetune.review` CLI shows idea,
   evidence, draft label; human accepts/edits). Store `checked_by`, `checked_at`, `original_label`, `final_label`.
   Mark `hand_checked: true`. **Hand-checked examples are the evaluation (test) set.**
4. **Split by idea, not by row** (80/20 via grouped split on `idea_id`, seed 42): no idea in both train and test.
   **Never** use ideas from the labelled verdict set (`eval/labelled/`): the split script asserts zero overlap
   (by idea id and by normalised text similarity ≥ 0.9) and fails otherwise.
5. Format: **one JSON object per line**, chat messages (system/user/assistant) exactly as in 09 §7:
   ```json
   {"messages":[{"role":"system","content":"Score the idea on demand_pain using rubric demand_pain@v3. Reply with JSON only."},
                {"role":"user","content":"IDEA: ... EVIDENCE: [card_8f21#c2] ... [card_77a1#c0] ..."},
                {"role":"assistant","content":"{\"score\": 3.5, \"confidence\": 0.7, \"evidence_ids\": [\"card_8f21#c2\"], \"reason\": \"...\"}"}],
    "meta":{"idea_id":"...","dimension":"demand_pain","level":3.5,"hand_checked":false,"rubric_version":"demand_pain@v3"}}
   ```
   Files: `train.jsonl`, `val.jsonl` (10% of train ideas, for validation loss), `test.jsonl` (hand-checked only).
   Commit them (small, text, no private data). `data/README.md` documents provenance + counts per cell.

## 2. Training (`jobs/finetune/train.ipynb` + `train.py`; Colab T4 / Kaggle P100)
| Setting | Start value (record actual in `jobs/finetune/runs/<run>.json`) |
|---|---|
| Base model | `Qwen/Qwen2.5-1.5B-Instruct` (alt `meta-llama/Llama-3.2-3B-Instruct`) |
| Method | QLoRA 4-bit (Unsloth `FastLanguageModel`), TRL `SFTTrainer`, loss on assistant tokens only |
| LoRA | r = 16, alpha = 32, dropout = 0.05, targets q,k,v,o,gate,up,down |
| LR / schedule | 2e-4, cosine, warmup 5% |
| Epochs | 2–3 (watch validation loss; early stop when val loss rises 2 evals in a row) |
| Batch | per-device 4, grad-accum 4 (effective 16) |
| Max seq len | 2048 |
| Seed | 42 |
Record: settings, train/val loss curve PNG, wall time, GPU type, final val loss → `runs/<run>.json` (+ PNG in report).

## 3. Merge, quantise, publish
`python -m finetune.export --run <run>` → merge adapter into base → GGUF `Q4_K_M` (~1 GB) → upload as **GitHub Release
asset** `scorer-lora-v1.gguf` (or HF Hub) + `sha256`. **Never commit weights to git** (`.gitignore` has `*.gguf, *.safetensors, *.bin`).
K8s Job `model-import` downloads the file **through M5** (allow-listed host `objects.githubusercontent.com` / `huggingface.co`)
into PVC `models`, verifies sha256.

## 4. Serving (M10 container `scorer`)
- Container = `llama.cpp` server (CPU) + FastAPI wrapper (`services/scorer`). Model path from env; `/ready` = model loaded
  and a warm-up completion succeeded.
- `POST /score`: build prompt (09 §7) → llama.cpp with **JSON-schema constrained decoding** → Pydantic validation +
  evidence-id subset check → **invalid → retry once → fallback to hosted model via M5 → log fallback** (trace
  `fallback: true`, metric `scorer_fallback_total{reason}`) → still invalid → abstain.
- Model tag (`scorer-lora@v1`) returned in every score → stored in `dimension_scores.model`.
- K8s requests/limits from measurement (`scripts/measure_resources.sh` → `kubectl top` under load: 20 concurrent scores);
  start guess requests cpu 1 / mem 2Gi, limits cpu 2 / mem 3Gi.

## 5. Evaluation (Guide §6.3): `python -m finetune.compare`
Run **base**, **fine-tuned**, and **hosted** on the **hand-checked test examples** with the identical prompt template.
One table in the report:

| Model | MAE vs human | Within 1 point | Valid JSON | Real evidence ids | Latency p50/p95 | Cost / score |
|---|---|---|---|---|---|---|
| base (Qwen2.5-1.5B) | | | | | | |
| fine-tuned (scorer-lora@v1) | | | | | | |
| hosted (e.g. gpt-4o-mini / claude-haiku) | | | | | | |

- *Real evidence ids* = share of outputs whose ids are all in the provided evidence.
- Latency measured in the cluster container (CPU); hosted measured through M5.
- Cost: hosted from LiteLLM cost; local = 0 marginal ($ / CPU-hour optional estimate).
- Save `eval/results/finetune_comparison.json` and a markdown table.
- **One example where fine-tuning did not help, explained** (e.g. risk dimension with rare legal blockers: few training
  examples per level → fine-tuned model under-scores risk; hosted model knows legal context). Required in the report.

## 6. Done criteria for M10/M10t
≥ 300 examples, ≥ 60 hand-checked and marked, split by idea, no overlap with labelled set, settings + loss recorded,
adapter merged, weights outside git, container serving `/score` `/health` `/ready`, schema validation + retry + fallback
+ logged fallback, comparison table with all six metrics, one failure case, requests/limits from measurement.
