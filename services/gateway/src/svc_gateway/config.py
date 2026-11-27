import re

from pydantic import Field, SecretStr, field_validator

from common.config import ServiceSettings

_ORIGIN = re.compile(r"https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?")


class Settings(ServiceSettings):
    """M1 gateway settings. Secrets and URLs come from the environment (K8s Secret / ConfigMap)."""

    service_name: str = "gateway"
    profile_url: str | None = None  # M2, e.g. http://profile.idea-validator.svc.cluster.local:8000
    orchestrator_url: str | None = None  # M3, e.g. http://orchestrator.idea-validator.svc:8000
    redis_url: SecretStr | None = None  # rate-limit counters and the run event channels
    jwt_secret: SecretStr | None = None
    jwt_ttl_s: int = 3600
    ui_origin: str | None = None  # the only origin CORS allows
    # Number of trusted reverse proxies in front of the gateway: 1 behind the ingress, 0 when
    # reached directly (the header is then ignored). The client IP is the X-Forwarded-For entry
    # that many places from the right. The ingress must overwrite the header, not append to it.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)
    body_limit_bytes: int = 32 * 1024
    ip_per_min: int = 100
    login_per_min: int = 5
    register_per_min: int = 10
    # Starting a run costs money (LLM and fetch budget), so it has its own per-user limit.
    user_runs_per_min: int = Field(default=10, ge=1)
    sse_per_min: int = Field(default=30, ge=1)  # new event streams per user and minute
    user_per_min: int = Field(default=120, ge=1)  # every authenticated request, per user
    sse_streams_per_user: int = Field(default=3, ge=1)  # open streams at one time
    sse_streams_global: int = Field(default=100, ge=1)  # open streams per replica
    sse_pool_size: int = Field(default=110, ge=1)  # Redis connections for pub/sub
    sse_max_frame_bytes: int = Field(default=65536, ge=1024)
    sse_heartbeat_s: float = Field(default=15.0, gt=0)
    sse_max_s: float = Field(default=900.0, gt=0)  # a stream is closed after this; clients resume
    upstream_timeout_s: float = 5.0
    min_password_length: int = 10

    @field_validator("ui_origin")
    @classmethod
    def _single_origin(cls, value: str | None) -> str | None:
        if value is not None and not _ORIGIN.fullmatch(value):
            raise ValueError("UI_ORIGIN must be one scheme://host[:port] origin, never '*'")
        return value
