"""Merge the LoRA adapter, convert to GGUF, quantise to Q4_K_M, write the sha256 (11 §3).

``python -m finetune.export --run scorer-lora-v1 --llama-cpp C:/.../llama``

Memory-safe on 16 GB RAM: the base model is loaded once in bf16 on the CPU with low-memory
loading, the adapter is merged in place and saved as 16-bit safetensors; the converter and
``llama-quantize`` then work from disk. Weights stay out of git (``*.gguf``/``*.safetensors``).
"""

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from .checksum import sha256_file, write_checksum

QUANT = "Q4_K_M"


def convert_command(llama_src: Path, merged_dir: Path, out_path: Path, outtype: str) -> list[str]:
    script = llama_src / "convert_hf_to_gguf.py"
    return [
        sys.executable,
        str(script),
        str(merged_dir),
        "--outfile",
        str(out_path),
        "--outtype",
        outtype,
    ]


def quantise_command(
    quantize_bin: Path, src: Path, out_path: Path, *, requantize: bool
) -> list[str]:
    cmd = [str(quantize_bin)]
    if requantize:
        cmd.append("--allow-requantize")
    return [*cmd, str(src), str(out_path), QUANT]


def merge_adapter(
    base_model: str, adapter_dir: Path, merged_dir: Path, *, free_base_cache: bool = False
) -> None:
    """Merge on the CPU in bf16. With ``free_base_cache`` the HF cache copy of the base is deleted
    once the merged weights are in RAM (disk-lean mode; the base can be downloaded again)."""
    import torch
    from huggingface_hub import scan_cache_dir
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model = AutoModelForCausalLM.from_pretrained(
        base_model, dtype=torch.bfloat16, low_cpu_mem_usage=True, device_map={"": "cpu"}
    )
    merged = PeftModel.from_pretrained(model, str(adapter_dir)).merge_and_unload()
    if free_base_cache:
        for repo in scan_cache_dir().repos:
            if repo.repo_id == base_model:
                strategy = scan_cache_dir().delete_revisions(
                    *[rev.commit_hash for rev in repo.revisions]
                )
                strategy.execute()
    merged_dir.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(merged_dir, safe_serialization=True)
    AutoTokenizer.from_pretrained(str(adapter_dir)).save_pretrained(merged_dir)


def export(
    *,
    run_id: str,
    base_model: str,
    out_dir: Path,
    llama_src: Path,
    quantize_bin: Path,
    lean: bool,
) -> dict[str, str]:
    """``lean``: free the base cache after merging, convert to Q8_0, then requantise to Q4_K_M
    (for a disk too small to hold bf16 + f16 copies). Otherwise f16 then Q4_K_M."""
    run_dir = out_dir / run_id
    adapter = run_dir / "adapter"
    merged = run_dir / "merged"
    outtype = "q8_0" if lean else "f16"
    mid = run_dir / f"{run_id}-{outtype}.gguf"
    final = run_dir / f"{run_id}.gguf"
    if not (adapter / "adapter_config.json").exists():
        raise FileNotFoundError(f"no adapter at {adapter}")
    merge_adapter(base_model, adapter, merged, free_base_cache=lean)
    subprocess.run(convert_command(llama_src, merged, mid, outtype), check=True)  # noqa: S603
    if lean:
        shutil.rmtree(merged)  # frees ~3 GB before quantising
    subprocess.run(  # noqa: S603
        quantise_command(quantize_bin, mid, final, requantize=lean), check=True
    )
    mid.unlink(missing_ok=True)
    write_checksum(final)
    info = {
        "gguf": str(final),
        "sha256": sha256_file(final),
        "size_bytes": str(final.stat().st_size),
        "quant": QUANT,
        "path": "q8_0 then requantised" if lean else "f16 then quantised",
    }
    (run_dir / "export.json").write_text(json.dumps(info, indent=2) + chr(10), encoding="utf-8")
    return info


def main(argv: Sequence[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="finetune.export")
    p.add_argument("--run", required=True)
    p.add_argument("--base-model", default="Qwen/Qwen2.5-1.5B-Instruct")
    p.add_argument("--out-dir", default="jobs/finetune/out")
    p.add_argument(
        "--llama-src", required=True, help="llama.cpp source dir (convert_hf_to_gguf.py)"
    )
    p.add_argument("--quantize-bin", required=True, help="path to llama-quantize")
    p.add_argument("--lean", action="store_true", help="disk-lean: Q8_0 then requantise")
    a = p.parse_args(argv)
    info = export(
        run_id=a.run,
        base_model=a.base_model,
        out_dir=Path(a.out_dir),
        llama_src=Path(a.llama_src),
        quantize_bin=Path(a.quantize_bin),
        lean=a.lean,
    )
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
