"""GET /registry response: versions and content hashes, never file contents."""

from common.schemas.base import Message


class RegistryEntry(Message):
    kind: str  # prompt | rubric | config
    name: str
    version: str
    sha256: str
    path: str


class ActiveVersions(Message):
    prompts: dict[str, str]  # agent -> prompt@vN selected in config/prompts.yaml
    rubrics: dict[str, str]  # dimension -> rubric@vN selected in config/verdict.yaml


class ModelInfo(Message):
    alias: str
    provider: str
    model: str
    sha256: str  # hash of the alias block (prices, endpoint); the API key is only a variable name


class RegistryView(Message):
    entries: list[RegistryEntry]
    active: ActiveVersions
    versions: dict[str, str]
    models: list[ModelInfo]
    in_sync: bool  # False when the tree breaks registry.lock.json (or the lock is missing)
    problems: list[str]
