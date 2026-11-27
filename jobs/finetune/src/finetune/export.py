"""Merge the LoRA adapter, convert to GGUF, quantise to Q4_K_M, write the sha256 (11 §3).

``python -m finetune.export --run scorer-lora-v1 --llama-cpp C:/.../llama``

Memory-safe on 16 GB RAM: the base model is loaded once in bf16 on the CPU with low-memory
loading, the adapter is merged in place and saved as 16-bit safetensors; the converter and
``llama-quantize`` then work from disk. Weights stay out of git (``*.gguf``/``*.safetensors``).
"""

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from .checksum import sha256_file, write_checksum

QUANT = "Q4_K_M"


def merge_adapter(base_model: str, adapter_dir: Path, merged_dir: Path) -> None:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model = AutoModelForCausalLM.from_pretrained(
        base_model, torch_dtype=torch.bfloat16, low_cpu_mem_usage=True, device_map={"": "cpu"}
    )
    merged = PeftModel.from_pretrained(model, str(adapter_dir)).merge_and_unload()
    merged_dir.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(merged_dir, safe_serialization=True)
    AutoTokenizer.from_pretrained(str(adapter_dir)).save_pretrained(merged_dir)


def convert_to_gguf(llama_src: Path, merged_dir: Path, f16_path: Path) -> None:
    script = llama_src / "convert_hf_to_gguf.py"
    cmd = [
        sys.executable,
        str(script),
        str(merged_dir),
        "--outfile",
        str(f16_path),
        "--outtype",
        "f16",
    ]
    subprocess.run(cmd, check=True)  # noqa: S603


def quantise(quantize_bin: Path, f16_path: Path, out_path: Path) -> None:
    subprocess.run([str(quantize_bin), str(f16_path), str(out_path), QUANT], check=True)  # noqa: S603


def export(
    *,
    run_id: str,
    base_model: str,
    out_dir: Path,
    llama_src: Path,
    quantize_bin: Path,
    keep_intermediate: bool,
) -> dict[str, str]:
    run_dir = out_dir / run_id
    adapter = run_dir / "adapter"
    merged = run_dir / "merged"
    f16 = run_dir / f"{run_id}-f16.gguf"
    final = run_dir / f"{run_id}.gguf"
    if not (adapter / "adapter_config.json").exists():
        raise FileNotFoundError(f"no adapter at {adapter}")
    merge_adapter(base_model, adapter, merged)
    convert_to_gguf(llama_src, merged, f16)
    quantise(quantize_bin, f16, final)
    write_checksum(final)
    if not keep_intermediate:
        f16.unlink(missing_ok=True)
    info = {
        "gguf": str(final),
        "sha256": sha256_file(final),
        "size_bytes": str(final.stat().st_size),
        "quant": QUANT,
    }
    (run_dir / "export.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
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
    p.add_argument("--keep-intermediate", action="store_true")
    a = p.parse_args(argv)
    info = export(
        run_id=a.run,
        base_model=a.base_model,
        out_dir=Path(a.out_dir),
        llama_src=Path(a.llama_src),
        quantize_bin=Path(a.quantize_bin),
        keep_intermediate=a.keep_intermediate,
    )
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
