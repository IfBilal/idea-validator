# ADR-010: fine-tune locally on an 8 GB GPU with plain transformers + PEFT (no Unsloth, no Colab)

- **Title:** Local QLoRA on the RTX 3060 Ti with transformers + PEFT + bitsandbytes; llama.cpp for export and serving
- **Status:** Accepted (2026-11-27), owner Dev A (gpu-session)
- **Context:** The spec (11 §2, CLAUDE.md §6) names Unsloth on Colab/Kaggle. The team has a Windows 11 PC with an
  RTX 3060 Ti (8 GB, Ampere, bf16), 16 GB RAM, one 476 GB disk with about 8 GB free, no WSL. The job is about 360-600 short
  examples on a 1.5B model: minutes per epoch on this GPU.
- **Options:** (1) Colab/Kaggle + Unsloth as written. (2) Unsloth on native Windows. (3) Plain transformers + PEFT +
  bitsandbytes, own tokenisation and loss mask, on the local GPU.
- **Decision:** Option 3. Same method as the spec (QLoRA 4-bit nf4, LoRA r=16 / alpha=32 / dropout 0.05 on q,k,v,o,gate,up,down,
  lr 2e-4, cosine, warmup 5%, early stopping on validation loss, seed 42, assistant-only loss, base
  `Qwen/Qwen2.5-1.5B-Instruct`); only the library differs. Batch 2 x accumulation 8 (effective 16) for 8 GB.
- **Why:** No account or upload of data is needed; runs are reproducible on a machine the team controls. Unsloth on native
  Windows depends on third-party Triton builds that are not reliable; its speed-up is irrelevant at this data size. The loss
  mask is our own tested function (`finetune.masking`), so "loss on assistant tokens only" is a verified fact, not a library
  option. The prompt is built by the production `PromptBuilder`, so training, serving and comparison see identical text.
- **Deviations from the spec table (recorded, not hidden):** per-device batch 2 (spec 4) x accumulation 8 (spec 4);
  optimiser `paged_adamw_8bit`; `max_seq_len` measured from the data rather than fixed at 2048; the evidence format is the
  production `<evidence id="...">` tags (the spec's `[card#c]` sketch is illustrative).
- **Disk-lean export:** only ~8 GB of disk is free. Export merges the adapter on the CPU in bf16 and converts to GGUF; if the
  f16 intermediate does not fit, the converter writes Q8_0 and `llama-quantize --allow-requantize` produces Q4_K_M. The
  quantisation damage is then measured (merged vs Q4_K_M on the validation examples) and reported, so the choice is visible.
- **Cost:** Heavy dependencies live in `jobs/finetune/requirements-gpu.txt` and a separate venv; the root uv lockfile and the
  Docker images stay light. Windows-specific: dataloader workers 0, no Triton.
- **AI use:** Written by AI; a human reviews the training settings and the comparison methodology.
