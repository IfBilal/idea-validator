"""Quantisation-damage check (A4): same prompts through the merged bf16 model and the Q4_K_M GGUF.

``python -m finetune.quantcheck hf --adapter ... --data val.jsonl --out hf.json``
``python -m finetune.quantcheck server --url http://127.0.0.1:8089 --data val.jsonl --out q4.json``
``python -m finetune.quantcheck diff hf.json q4.json``
"""

import argparse
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .compare import LlamaServerModel
from .metrics import score_outputs
from .schema import SCORE_JSON_SCHEMA, prompt_evidence_ids


def _rows(path: Path, limit: int) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines if x.strip()][:limit]


def generate_hf(
    adapter: Path, base: str, rows: Sequence[Mapping[str, Any]], max_new: int
) -> list[str]:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(str(adapter))
    # direct-to-GPU safetensors loading segfaults on this Windows setup: load on the CPU, move after
    model = AutoModelForCausalLM.from_pretrained(base, dtype=torch.bfloat16)
    model = PeftModel.from_pretrained(model, str(adapter)).merge_and_unload().eval().to("cuda")
    outs: list[str] = []
    for row in rows:
        msgs = row["messages"][:2]
        ids = tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt")
        ids = (ids["input_ids"] if hasattr(ids, "keys") else ids).to(model.device)
        with torch.no_grad():
            out = model.generate(ids, max_new_tokens=max_new, do_sample=False)
        outs.append(tok.decode(out[0, ids.shape[1] :], skip_special_tokens=True))
    return outs


def generate_server(
    url: str, rows: Sequence[Mapping[str, Any]], max_new: int
) -> tuple[list[str], list[float]]:
    model = LlamaServerModel(url, schema=SCORE_JSON_SCHEMA)
    outs, secs = [], []
    for row in rows:
        gen = model.generate(
            system=row["messages"][0]["content"],
            user=row["messages"][1]["content"],
            max_tokens=max_new,
        )
        outs.append(gen.text)
        secs.append(gen.seconds)
    return outs, secs


def summarise(rows: Sequence[Mapping[str, Any]], outs: Sequence[str]) -> dict[str, Any]:
    items = [
        {
            "dimension": r["meta"]["dimension"],
            "human": 0.0 if r["meta"]["level"] is None else float(r["meta"]["level"]),
            "provided": prompt_evidence_ids(r["messages"][1]["content"]),
            "output": o,
        }
        for r, o in zip(rows, outs, strict=True)
    ]
    return {"metrics": score_outputs(items), "outputs": list(outs)}


def score_of(text: str) -> float | None:
    try:
        value = json.loads(text).get("score")
    except (ValueError, AttributeError):
        return None
    return None if value is None else float(value)


def diff(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    pairs = [(score_of(x), score_of(y)) for x, y in zip(a["outputs"], b["outputs"], strict=True)]
    both = [(x, y) for x, y in pairs if x is not None and y is not None]
    return {
        "n": len(pairs),
        "both_scored": len(both),
        "mean_abs_score_diff": sum(abs(x - y) for x, y in both) / len(both) if both else None,
        "identical_score_rate": sum(1 for x, y in both if x == y) / len(both) if both else None,
        "mae_a": a["metrics"]["mae"],
        "mae_b": b["metrics"]["mae"],
        "valid_json_a": a["metrics"]["valid_json_rate"],
        "valid_json_b": b["metrics"]["valid_json_rate"],
    }


def main(argv: Sequence[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="finetune.quantcheck")
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hf")
    h.add_argument("--adapter", required=True)
    h.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    s = sub.add_parser("server")
    s.add_argument("--url", required=True)
    for q in (h, s):
        q.add_argument("--data", required=True)
        q.add_argument("--out", required=True)
        q.add_argument("--limit", type=int, default=40)
        q.add_argument("--max-new", type=int, default=400)
    d = sub.add_parser("diff")
    d.add_argument("a")
    d.add_argument("b")
    a = p.parse_args(argv)
    if a.cmd == "diff":
        res = diff(json.loads(Path(a.a).read_text()), json.loads(Path(a.b).read_text()))
        print(json.dumps(res, indent=2))
        return
    rows = _rows(Path(a.data), a.limit)
    start = time.time()
    if a.cmd == "hf":
        outs = generate_hf(Path(a.adapter), a.base, rows, a.max_new)
    else:
        outs, _ = generate_server(a.url, rows, a.max_new)
    result = summarise(rows, outs)
    result["wall_s"] = round(time.time() - start, 1)
    Path(a.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["metrics"], indent=2))


if __name__ == "__main__":
    main()
