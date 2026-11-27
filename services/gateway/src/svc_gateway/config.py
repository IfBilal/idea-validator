from pydantic import SecretStr

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M1 gateway settings. Secrets and URLs come from the environment (K8s Secret / ConfigMap)."""

    service_name: str = "gateway"
    profile_url: str | None = None  # M2, e.g. http://profile.idea-validator.svc.cluster.local:8000
    redis_url: SecretStr | None = None
    jwt_secret: SecretStr | None = None
    jwt_ttl_s: int = 3600
    ui_origin: str | None = None  # the only origin CORS allows
    trust_forwarded_for: bool = (
        False  # True only behind the ingress (NetworkPolicy keeps others out)
    )
    body_limit_bytes: int = 32 * 1024
    ip_per_min: int = 100
    login_per_min: int = 5
    register_per_min: int = 10
    upstream_timeout_s: float = 5.0
    min_password_length: int = 10
