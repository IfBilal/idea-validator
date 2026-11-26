from common.config import ServiceSettings


class Settings(ServiceSettings):
    """Service settings. Add module-specific fields here (read from env / ConfigMap)."""

    service_name: str = "kb"
