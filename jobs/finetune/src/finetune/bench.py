"""CPU serving benchmark for the GGUF under llama.cpp (A4, P8-T07 inputs for Kubernetes limits).

``python -m finetune.bench --gguf X.gguf --server llama-server.exe --prompts val.jsonl``

Starts ``llama-server`` on loopback with one slot (the pod's real shape), sends realistic prompts
with JSON-schema constrained decoding, and records latency p50/p95 and peak resident memory.
"""

import argparse
import json
import subprocess
import time
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .compare import LlamaServerModel
from .metrics import latency_stats
from .schema import SCORE_JSON_SCHEMA


def server_command(server: Path, gguf: Path, *, port: int, threads: int, ctx: int) -> list[str]:
    return [
        str(server),
        "-m",
        str(gguf),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "-t",
        str(threads),
        "-c",
        str(ctx),
        "-np",
        "1",
        "--no-webui",
    ]


def summarise(
    latencies: Sequence[float],
    *,
    rss_mb: Sequence[float],
    prompt_tokens: Sequence[int],
    threads: int,
) -> dict[str, Any]:
    return {
        "latency_s": latency_stats(latencies),
        "n": len(latencies),
        "rss_mb_max": max(rss_mb) if rss_mb else None,
        "rss_mb_mean": sum(rss_mb) / len(rss_mb) if rss_mb else None,
        "prompt_tokens_mean": round(sum(prompt_tokens) / len(prompt_tokens))
        if prompt_tokens
        else None,
        "threads": threads,
    }


def _wait_ready(base: str, proc: subprocess.Popen[bytes], timeout_s: float = 180) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("llama-server exited early")
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(1)
    raise TimeoutError("llama-server did not become ready")


def run_bench(
    gguf: Path,
    server: Path,
    prompts: Sequence[tuple[str, str]],
    *,
    threads: int,
    ctx: int = 4096,
    port: int = 8089,
    max_tokens: int = 400,
) -> dict[str, Any]:
    import psutil

    proc = subprocess.Popen(  # noqa: S603
        server_command(server, gguf, port=port, threads=threads, ctx=ctx),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base, proc)
        ps = psutil.Process(proc.pid)
        model = LlamaServerModel(base, schema=SCORE_JSON_SCHEMA)
        rss: list[float] = [ps.memory_info().rss / 2**20]
        model.generate(system=prompts[0][0], user=prompts[0][1], max_tokens=max_tokens)  # warm-up
        lat: list[float] = []
        outputs = 0
        for system, user in prompts:
            gen = model.generate(system=system, user=user, max_tokens=max_tokens)
            lat.append(gen.seconds)
            outputs += 1 if gen.text.strip().startswith("{") else 0
            rss.append(ps.memory_info().rss / 2**20)
        out = summarise(lat, rss_mb=rss, prompt_tokens=[], threads=threads)
        out["valid_object_rate"] = outputs / len(prompts)
        return out
    finally:
        proc.terminate()
        proc.wait(timeout=30)


def main(argv: Sequence[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="finetune.bench")
    p.add_argument("--gguf", required=True)
    p.add_argument("--server", required=True)
    p.add_argument("--prompts", required=True, help="JSONL of training-format examples")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--out", default="")
    a = p.parse_args(argv)
    rows = [json.loads(x) for x in Path(a.prompts).read_text(encoding="utf-8").splitlines() if x]
    prompts = [(r["messages"][0]["content"], r["messages"][1]["content"]) for r in rows[: a.limit]]
    result = run_bench(Path(a.gguf), Path(a.server), prompts, threads=a.threads)
    text = json.dumps(result, indent=2)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
