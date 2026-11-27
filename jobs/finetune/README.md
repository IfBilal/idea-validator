# finetune (M10t): the scoring-model pipeline

Fine-tunes `Qwen/Qwen2.5-1.5B-Instruct` with QLoRA to score ONE idea on ONE dimension from retrieved evidence
(docs/harness/11, 09 §7). The model never outputs a verdict: only `score`, `confidence`, `evidence_ids`, `reason`.
Decisions and deviations: `docs/decisions/ADR-010-finetune-on-local-8gb-gpu.md`.

## Two environments
* **Root workspace (light, CI):** `uv sync --all-packages`. The pure-Python pieces and their tests (`jobs/finetune/tests`).
* **GPU venv (heavy):** `uv venv --python 3.12 ft-venv`, CUDA torch, then `jobs/finetune/requirements-gpu.txt`. Only
  `train`, `export` and the model runners need it.

## Pipeline (run from the repo root)
| Step | Command | Needs |
|---|---|---|
| Candidates (P8-T01) | `finetune.candidates.build_candidates` with the KB retriever | KB snapshot (#29/#30) |
| Draft labels (P8-T02) | `python -m finetune.draft_labels --candidates ... --out data/drafts.jsonl` | `GROQ_API_KEY` in `.env` |
| Human review (P8-T03) | `python -m finetune.review --reviewer taimoor` (and `bilal`) | **a person at a terminal** |
| Dataset (P8-T04) | `python -m finetune.dataset` | >= 60 hand-checked examples |
| Train (P8-T05) | `python -m finetune.train --run-id scorer-lora-v1` | GPU venv |
| Export (P8-T06) | `python -m finetune.export --run scorer-lora-v1 --llama-src ... --quantize-bin ...` | GPU venv, llama.cpp |
| Serve benchmark | `python -m finetune.bench --gguf ... --server llama-server --prompts data/val.jsonl` | llama.cpp CPU build |
| Compare (P8-T08) | `finetune.compare.compare_models` on `data/test.jsonl` | hand-checked test set |

## Rules the code enforces (each has a test)
* Targets cite only evidence ids present in their own prompt; no evidence means abstain; no verdict wording or extra keys.
* Split by idea; zero overlap with `eval/labelled` by id and by text similarity >= 0.9, asserted on every build.
* Only `finetune.review` (human, TTY) creates `hand_checked`; the dataset build fails if fewer than 60 are reviewed.
* The comparison refuses synthetic or unchecked rows and gives every model the same prompt and token budget.
* Synthetic data (`finetune.synthetic`) is marked `synthetic: true`, git-ignored and refused by the dataset builder.
* No weights, no secrets in git (`*.gguf`, `*.safetensors`, `.env` are ignored).
