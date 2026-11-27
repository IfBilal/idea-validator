from pydantic import SecretStr, model_validator

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M4 settings. Secrets (database, API tokens) come from a K8s Secret, never from git."""

    service_name: str = "scouts"
    database_url: SecretStr | None = None
    tools_url: str | None = None  # M5, the only way to the internet (CLAUDE.md rule 4)

    # Optional credentials: a source that needs one stays off until it is set. Never logged.
    github_token: SecretStr | None = None
    reddit_client_id: SecretStr | None = None
    reddit_client_secret: SecretStr | None = None
    producthunt_token: SecretStr | None = None

    # M5 does not forward request headers or POST bodies today (GET only). The flag exists so the
    # authenticated connectors can be switched on the day M5 grows that contract.
    m5_forward_auth: bool = False
    # Second switch: M5 must first bypass cache and logging for requests with headers/body, accept
    # auth headers only for the credential's own host, and redact Authorization/access_token.
    m5_supports_auth: bool = False

    fetch_timeout_s: float = 40.0  # M5 has its own 30 s deadline; this one must be larger
    fetch_retries: int = 1
    breaker_threshold: int = 3
    breaker_cooldown_s: int = 1800
    scout_max_limit: int = 500  # hard cap for one POST /scout
    scout_timeout_s: float = 120.0  # wall-clock bound of one POST /scout
    default_limit: int = 1000  # CLI default when --limit is not given
    max_body_bytes: int = 4096

    @model_validator(mode="after")
    def _forwarding_needs_both_switches(self) -> "Settings":
        if self.m5_forward_auth and not self.m5_supports_auth:
            raise ValueError("M5_FORWARD_AUTH requires M5_SUPPORTS_AUTH=true (M5 hardening first)")
        return self
