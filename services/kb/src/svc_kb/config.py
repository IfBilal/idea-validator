from pydantic import SecretStr

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M6 settings: the database URL is a Secret, Qdrant's address a ConfigMap value."""

    service_name: str = "kb"
    database_url: SecretStr | None = None
    ingest_token: SecretStr | None = None  # separate from internal_token: only ingestion may write
    qdrant_url: str | None = None
    qdrant_collection: str = "chunks"
    model_cache_dir: str | None = None
    max_body_bytes: int = 1_000_000
