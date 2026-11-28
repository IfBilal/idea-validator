from pathlib import Path

from pydantic import SecretStr

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M13 settings. The database URL comes from a K8s Secret, the trace directory from a PVC."""

    service_name: str = "agentops"
    database_url: SecretStr | None = None
    trace_dir: Path = Path("/var/lib/agentops/traces")
    max_batch: int = 100  # records per POST /traces
    max_body_bytes: int = 2 * 1024 * 1024
    max_record_bytes: int = 16 * 1024  # one sanitised record; larger ones are rejected
    max_run_records: int = 5000  # records returned for one run (the rest is flagged truncated)
    runs_default_limit: int = 20
    runs_max_limit: int = 100
