#!/usr/bin/env python3
"""Turn `kubectl top pod` samples taken under load into requests and limits (P11-T01).

Input lines (from the sampler in measure_resources.sh): ``<pod> <cpu e.g. 12m> <memory e.g. 80Mi>``.
Pods of one Deployment share a name prefix (``gateway-5d8f7-abcde``): they are grouped by app.
Rule: request = p95 of the samples x 1.2, limit = max x 2, both rounded up (CPU to 10m, memory to 16Mi),
never below a floor so an idle pod still gets something sane. Output is JSON on stdout.
"""

import json
import math
import re
import sys
from collections import defaultdict

CPU_FLOOR_M, MEM_FLOOR_MI = 20, 64
_POD = re.compile(r"^(?P<app>.+?)-[a-f0-9]{6,10}-[a-z0-9]{5}$|^(?P<sts>.+)-\d+$")


def app_of(pod: str) -> str:
    m = _POD.match(pod)
    return (m.group("app") or m.group("sts")) if m else pod


def parse_cpu(value: str) -> int:
    return int(value[:-1]) if value.endswith("m") else int(float(value) * 1000)


def parse_mem(value: str) -> int:
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024}
    for unit, factor in units.items():
        if value.endswith(unit):
            return math.ceil(float(value[: -len(unit)]) * factor)
    return math.ceil(int(value) / (1024 * 1024))


def p95(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


def _up(value: float, step: int) -> int:
    return int(math.ceil(value / step) * step)


def summarise(lines: list[str]) -> dict[str, dict[str, object]]:
    cpu: dict[str, list[int]] = defaultdict(list)
    mem: dict[str, list[int]] = defaultdict(list)
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        app = app_of(parts[0])
        cpu[app].append(parse_cpu(parts[1]))
        mem[app].append(parse_mem(parts[2]))
    out: dict[str, dict[str, object]] = {}
    for app in sorted(cpu):
        c, m = cpu[app], mem[app]
        out[app] = {
            "samples": len(c),
            "cpu_m": {"p95": p95(c), "max": max(c)},
            "mem_mi": {"p95": p95(m), "max": max(m)},
            "requests": {
                "cpu": f"{max(CPU_FLOOR_M, _up(p95(c) * 1.2, 10))}m",
                "memory": f"{max(MEM_FLOOR_MI, _up(p95(m) * 1.2, 16))}Mi",
            },
            "limits": {
                "cpu": f"{max(2 * CPU_FLOOR_M, _up(max(c) * 2, 10))}m",
                "memory": f"{max(2 * MEM_FLOOR_MI, _up(max(m) * 2, 16))}Mi",
            },
        }
    return out


if __name__ == "__main__":
    json.dump(summarise(sys.stdin.read().splitlines()), sys.stdout, indent=2, sort_keys=True)
    print()
