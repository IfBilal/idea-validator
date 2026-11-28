from pathlib import Path

from pydantic import SecretStr

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M13 settings. The database URL comes from a K8s Secret, the trace directory from a PVC."""

    service_name: str = "agentops"
    database_url: SecretStr | None = None
    trace_dir: Path = Path("/var/lib/agentops/traces")
    # prompts/, rubrics/, config/ and registry.lock.json as shipped in the image (GET /registry)
    registry_dir: Path = Path("/app/registry")
    drift_dir: Path | None = None  # drift reports; default: <trace_dir>/drift
    max_drift_bytes: int = 256 * 1024
    max_batch: int = 100  # records per POST /traces
    max_body_bytes: int = 2 * 1024 * 1024
    max_record_bytes: int = 16 * 1024  # one sanitised record; larger ones are rejected
    runs_default_limit: int = 20
    runs_max_limit: int = 100
    max_run_bytes: int = 8 * 1024 * 1024  # one run's JSONL file
    max_run_records: int = 5000  # records per run (also the most a read returns)
    # under this much free space ingest answers 507 and /ready fails
    min_free_bytes: int = 64 * 1024 * 1024
