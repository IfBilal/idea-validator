"""Job settings: environment only (CLAUDE.md rules 10-12); values come from ConfigMap/Secret."""

from pathlib import Path

from pydantic import Field, SecretStr

from common.config import ServiceSettings
from ingestion.dedupe import DedupeConfig

MAX_BATCH = 50  # M6 refuses more than 50 cards per POST /ingest


class Settings(ServiceSettings):
    service_name: str = "ingestion"
    kb_url: str | None = None
    ingest_token: SecretStr | None = None  # M6 write endpoints (Secret INGEST_TOKEN)
    database_url: SecretStr | None = None  # the scouts' raw_pages table (read only)
    ingestion_batch_size: int = Field(default=MAX_BATCH, ge=1, le=MAX_BATCH)
    ingestion_max_retries: int = Field(default=2, ge=0, le=2)
    ingestion_timeout_s: float = Field(default=30.0, gt=0, le=300)
    ingestion_minhash_threshold: float = Field(default=0.9, gt=0, le=1)
    ingestion_minhash_num_perm: int = Field(default=128, ge=1)
    ingestion_minhash_bands: int = Field(default=32, ge=1)
    ingestion_minhash_seed: int = 1
    ingestion_raw_pages_schema: str = "scouts"
    ingestion_state_dir: Path = Path("/tmp/ingestion")  # noqa: S108 (the Job's emptyDir)

    def dedupe_config(self) -> DedupeConfig:
        return DedupeConfig(
            threshold=self.ingestion_minhash_threshold,
            num_perm=self.ingestion_minhash_num_perm,
            bands=self.ingestion_minhash_bands,
            seed=self.ingestion_minhash_seed,
        )
