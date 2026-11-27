from pathlib import Path

from common.config import ServiceSettings


class Settings(ServiceSettings):
    """M10 settings (env / ConfigMap).

    Two modes. Hosted-only: ``LLM_URL`` set, no ``LOCAL_LLM_URL``. Local-first: ``LOCAL_LLM_URL``
    points at the llama.cpp server holding the fine-tuned GGUF; ``LLM_URL`` (if set) is the hosted
    fallback through M5.
    """

    service_name: str = "scorer"
    # M5 tool layer base URL; hosted-model calls go to <llm_url>/llm/v1/chat/completions.
    llm_url: str | None = None
    llm_timeout_s: float = 60.0  # config/budgets.yaml timeouts_s.llm
    llm_max_retries: int = 2  # config/budgets.yaml max_retries
    model_name: str = "hosted-default"  # recorded in DimensionScore.model for hosted answers
    # llama.cpp server (same pod, loopback) with the fine-tuned model; unset = hosted-only mode
    local_llm_url: str | None = None
    local_model_name: str = "scorer-lora@v1"
    local_timeout_s: float = 120.0  # CPU decoding of one score takes seconds, not milliseconds
    rubrics_dir: Path = Path("/app/rubrics")
    prompts_dir: Path = Path("/app/prompts")
