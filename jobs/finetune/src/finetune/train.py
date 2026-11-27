"""QLoRA fine-tuning of the scoring model (docs/harness/11 §2) for an 8 GB GPU.

``python -m finetune.train --data-dir jobs/finetune/data --run-id scorer-lora-v1``

Plain transformers + PEFT + bitsandbytes (no Unsloth: it is not reliable on native Windows; the
choice is recorded in ADR-010). Loss is on assistant tokens only (``masking.build_labels``).
Heavy imports live inside functions so the pure-Python test suite never needs a GPU stack.
"""

import argparse
import json
import platform
import time
from pathlib import Path
from typing import Any

from .masking import build_labels
from .runs import RunRecord, write_run
from .schema import load_jsonl

BASE_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def _encode(tokenizer: Any, msgs: list[dict[str, str]], *, generation: bool = False) -> list[int]:
    """Token ids of a chat; newer transformers return a mapping, older a plain list."""
    ids = tokenizer.apply_chat_template(msgs, add_generation_prompt=generation, tokenize=True)
    return list(ids["input_ids"] if hasattr(ids, "keys") else ids)


def tokenize_rows(tokenizer: Any, rows: list[Any], max_len: int) -> list[dict[str, list[int]]]:
    """Chat-template tokenisation with the prompt masked out of the loss."""
    out: list[dict[str, list[int]]] = []
    for ex in rows:
        msgs = [m.model_dump() for m in ex.messages]
        prompt_ids = _encode(tokenizer, msgs[:2], generation=True)
        ids, labels, _ = build_labels(prompt_ids, _encode(tokenizer, msgs), max_len=max_len)
        out.append({"input_ids": ids, "labels": labels, "attention_mask": [1] * len(ids)})
    return out


def length_report(tokenizer: Any, rows: list[Any]) -> dict[str, Any]:
    lengths = sorted(len(_encode(tokenizer, [m.model_dump() for m in ex.messages])) for ex in rows)
    if not lengths:
        return {"n": 0, "max": 0}

    def pick(q: float) -> int:
        return lengths[min(len(lengths) - 1, int(q * len(lengths)))]

    return {
        "n": len(lengths),
        "p50": pick(0.5),
        "p95": pick(0.95),
        "p99": pick(0.99),
        "max": lengths[-1],
    }


class Collator:
    def __init__(self, pad_id: int) -> None:
        self.pad_id = pad_id

    def __call__(self, batch: list[dict[str, list[int]]]) -> dict[str, Any]:
        import torch

        width = max(len(b["input_ids"]) for b in batch)
        ids = [b["input_ids"] + [self.pad_id] * (width - len(b["input_ids"])) for b in batch]
        lab = [b["labels"] + [-100] * (width - len(b["labels"])) for b in batch]
        att = [b["attention_mask"] + [0] * (width - len(b["attention_mask"])) for b in batch]
        return {
            "input_ids": torch.tensor(ids),
            "labels": torch.tensor(lab),
            "attention_mask": torch.tensor(att),
        }


def hardware_info() -> dict[str, Any]:
    import torch

    info: dict[str, Any] = {"os": platform.platform(), "python": platform.python_version()}
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        info.update(gpu=props.name, vram_gb=round(props.total_memory / 2**30, 2))
    return info


def library_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    out = {}
    for pkg in ("torch", "transformers", "peft", "bitsandbytes", "accelerate", "datasets"):
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = "missing"
    return out


def plot_losses(curve: list[dict[str, Any]], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    train = [(c["step"], c["train_loss"]) for c in curve if "train_loss" in c]
    val = [(c["step"], c["val_loss"]) for c in curve if "val_loss" in c]
    fig, ax = plt.subplots(figsize=(7, 4))
    if train:
        ax.plot(*zip(*train, strict=True), label="train loss")
    if val:
        ax.plot(*zip(*val, strict=True), marker="o", label="validation loss")
    ax.set_xlabel("optimizer step")
    ax.set_ylabel("loss")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def run_training(args: argparse.Namespace) -> Path:
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        EarlyStoppingCallback,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    set_seed(args.seed)
    data_dir = Path(args.data_dir)
    train_rows = load_jsonl(str(data_dir / "train.jsonl"))
    val_rows = load_jsonl(str(data_dir / "val.jsonl"))
    synthetic = any(r.meta.synthetic for r in train_rows + val_rows)
    if synthetic and not args.allow_synthetic:
        raise SystemExit("synthetic data found: pass --allow-synthetic for a dry run only")

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    lengths = length_report(tokenizer, train_rows + val_rows)
    max_len = args.max_len or min(2048, max(256, ((lengths["max"] + 63) // 64) * 64))
    train_ds = tokenize_rows(tokenizer, train_rows, max_len)
    val_ds = tokenize_rows(tokenizer, val_rows, max_len)

    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=quant,
        torch_dtype=torch.bfloat16,
        device_map={"": 0},
        attn_implementation="sdpa",
    )
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    lora = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=0.05,
        target_modules=TARGET_MODULES,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora)
    trainable, total = model.get_nb_trainable_parameters()

    out_dir = Path(args.out_dir) / args.run_id
    steps_per_epoch = max(1, len(train_ds) // (args.batch * args.accum))
    eval_every = args.eval_every or max(1, steps_per_epoch // 2)
    targs = TrainingArguments(
        output_dir=str(out_dir / "checkpoints"),
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=min(args.batch, 2),
        gradient_accumulation_steps=args.accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        optim="paged_adamw_8bit",
        bf16=True,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=1,
        eval_strategy="steps",
        eval_steps=eval_every,
        save_strategy="steps",
        save_steps=eval_every,
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        seed=args.seed,
        data_seed=args.seed,
        report_to=[],
        remove_unused_columns=False,
        dataloader_num_workers=0,
    )
    trainer = Trainer(
        model=model,
        args=targs,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        data_collator=Collator(tokenizer.pad_token_id),
        callbacks=[EarlyStoppingCallback(early_stopping_patience=2)],
    )
    torch.cuda.reset_peak_memory_stats()
    start = time.time()
    result = trainer.train()
    wall = time.time() - start
    peak = torch.cuda.max_memory_allocated() / 2**30
    adapter_dir: Path = out_dir / "adapter"
    trainer.model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)

    curve: list[dict[str, Any]] = []
    for rec in trainer.state.log_history:
        if "loss" in rec:
            curve.append({"step": rec["step"], "train_loss": rec["loss"]})
        if "eval_loss" in rec:
            curve.append({"step": rec["step"], "val_loss": rec["eval_loss"]})
    best_val = trainer.state.best_metric
    n_tokens = sum(len(r["input_ids"]) for r in train_ds)
    plot_losses(curve, out_dir / "loss.png")
    record = RunRecord(
        run_id=args.run_id,
        base_model=args.base_model,
        settings={
            "method": "QLoRA 4-bit nf4, plain transformers+PEFT+bitsandbytes",
            "lora_r": args.lora_r,
            "lora_alpha": args.lora_alpha,
            "lora_dropout": 0.05,
            "target_modules": TARGET_MODULES,
            "learning_rate": args.lr,
            "schedule": "cosine",
            "warmup_ratio": 0.05,
            "epochs": args.epochs,
            "max_steps": args.max_steps,
            "per_device_batch": args.batch,
            "grad_accum": args.accum,
            "effective_batch": args.batch * args.accum,
            "max_seq_len": max_len,
            "optimizer": "paged_adamw_8bit",
            "bf16": True,
            "seed": args.seed,
            "eval_every_steps": eval_every,
            "early_stopping_patience_evals": 2,
            "trainable_params": trainable,
            "total_params": total,
            "n_train": len(train_ds),
            "n_val": len(val_ds),
        },
        hardware=hardware_info(),
        versions=library_versions(),
        metrics={
            "final_train_loss": result.training_loss,
            "best_val_loss": best_val,
            "steps": trainer.state.global_step,
            "peak_vram_gb": round(peak, 3),
            "tokens_per_s": round(n_tokens * trainer.state.epoch / wall, 1) if wall else None,
            "token_lengths": lengths,
        },
        wall_time_s=round(wall, 1),
        synthetic=synthetic,
        loss_curve=curve,
    )
    path = write_run(record, args.runs_dir)
    print(json.dumps({"run": str(path), "peak_vram_gb": round(peak, 3), "wall_s": round(wall, 1)}))
    return adapter_dir


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="finetune.train")
    p.add_argument("--data-dir", default="jobs/finetune/data")
    p.add_argument("--out-dir", default="jobs/finetune/out")
    p.add_argument("--runs-dir", default="jobs/finetune/runs")
    p.add_argument("--run-id", required=True)
    p.add_argument("--base-model", default=BASE_MODEL)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--epochs", type=float, default=3)
    p.add_argument("--max-steps", type=int, default=-1)
    p.add_argument("--batch", type=int, default=2)
    p.add_argument("--accum", type=int, default=8)
    p.add_argument("--max-len", type=int, default=0)
    p.add_argument("--eval-every", type=int, default=0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--allow-synthetic", action="store_true")
    return p


def main() -> None:
    run_training(build_parser().parse_args())


if __name__ == "__main__":
    main()
