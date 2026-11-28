"""scripts/measure_resources.py: samples under load become requests and limits."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "measure_resources", ROOT / "scripts" / "measure_resources.py"
)
assert spec and spec.loader
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)


def test_pods_of_one_deployment_and_statefulset_are_grouped() -> None:
    assert mr.app_of("gateway-5d8f7c9b6-abcde") == "gateway"
    assert mr.app_of("kb-6b9f4d7c8d-x7k2p") == "kb"
    assert mr.app_of("postgres-0") == "postgres"
    assert mr.app_of("redis-tools-0") == "redis-tools"


def test_units_are_parsed() -> None:
    assert mr.parse_cpu("12m") == 12 and mr.parse_cpu("1") == 1000
    assert (
        mr.parse_mem("80Mi") == 80 and mr.parse_mem("2048Ki") == 2 and mr.parse_mem("1Gi") == 1024
    )


def test_requests_come_from_p95_and_limits_from_max() -> None:
    cpu = [10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38, 40, 42, 44, 46, 100]
    lines = [f"gateway-5d8f7c9b6-abcde {c}m {100 + c}Mi" for c in cpu]
    out = mr.summarise(lines)["gateway"]
    assert out["samples"] == 20 and out["cpu_m"] == {"p95": 46, "max": 100}
    assert out["requests"] == {"cpu": "60m", "memory": "176Mi"}  # 46*1.2 -> 60m ; 146*1.2 -> 176Mi
    assert out["limits"] == {
        "cpu": "200m",
        "memory": "400Mi",
    }  # max cpu 100m x 2 ; max memory 200Mi x 2


def test_an_idle_pod_still_gets_the_floor_and_junk_lines_are_ignored() -> None:
    out = mr.summarise(["agentops-6b9f4d7c8d-x7k2p 1m 5Mi", "garbage", ""])
    assert out["agentops"]["requests"] == {"cpu": "20m", "memory": "64Mi"}
    assert out["agentops"]["limits"] == {"cpu": "40m", "memory": "128Mi"}
