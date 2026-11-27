"""Request/response of ``POST /llm/v1/chat/completions`` (docs/harness/05; OpenAI-compatible)."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, computed_field, field_serializer

from common.schemas.base import Message


class ChatMessage(Message):
    role: Literal["system", "user", "assistant"]
    content: Annotated[str, Field(min_length=1, max_length=50_000)]


class LlmRequest(Message):
    run_id: Annotated[str, Field(min_length=1, max_length=80)]
    agent: Annotated[str, Field(min_length=1, max_length=40)]
    model: Annotated[str, Field(min_length=1, max_length=40)]  # an alias from config/llm.yaml
    messages: Annotated[list[ChatMessage], Field(min_length=1, max_length=50)]
    max_tokens: Annotated[int, Field(ge=1, le=4096)] = 800
    temperature: Annotated[float, Field(ge=0, le=2)] = 0
    response_format: Literal["json", "text"] | None = None


class LlmResponse(Message):
    content: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    latency_ms: int
    provider: str
    model: str

    @field_serializer("cost_usd")
    def _cost(self, value: Decimal) -> float:
        return float(value)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def choices(self) -> list[dict[str, object]]:
        return [{"index": 0, "message": {"role": "assistant", "content": self.content}}]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usage(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.tokens_in,
            "completion_tokens": self.tokens_out,
            "total_tokens": self.tokens_in + self.tokens_out,
        }
