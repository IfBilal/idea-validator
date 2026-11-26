import ast
import json
from collections.abc import Callable
from pathlib import Path

from conftest import WORKED_EXAMPLE, WORKED_EXAMPLE_CONF

from svc_verdict.models.inputs import EngineInput
from svc_verdict.services import engine
from svc_verdict.services.engine import evaluate
from svc_verdict.services.engine_config import EngineConfig

Build = Callable[..., EngineInput]
FORBIDDEN = {
    "random", "secrets", "uuid", "time", "datetime", "os", "socket", "ssl", "subprocess",
    "httpx", "requests", "urllib", "aiohttp", "asyncio", "sqlalchemy", "redis", "openai",
    "litellm", "pydantic_ai", "langgraph", "fastapi",
}  # fmt: skip


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_engine_core_imports_nothing_that_could_break_determinism_or_purity() -> None:
    assert not _imports(Path(engine.__file__)) & FORBIDDEN


def test_same_input_gives_byte_identical_output(build: Build, cfg: EngineConfig) -> None:
    inp = build("portfolio", WORKED_EXAMPLE, WORKED_EXAMPLE_CONF)
    first = json.dumps(evaluate(inp, cfg).to_dict(), sort_keys=True)
    second = json.dumps(evaluate(inp, cfg).to_dict(), sort_keys=True)
    assert first == second


def test_the_order_of_the_scores_does_not_matter(build: Build, cfg: EngineConfig) -> None:
    inp = build("portfolio", WORKED_EXAMPLE, WORKED_EXAMPLE_CONF)
    shuffled = inp.model_copy(update={"scores": list(reversed(inp.scores))})
    assert evaluate(inp, cfg).to_dict() == evaluate(shuffled, cfg).to_dict()


def test_inputs_with_missing_or_duplicate_dimensions_are_rejected(build: Build) -> None:
    import pytest
    from pydantic import ValidationError

    inp = build()
    payload = inp.model_dump()
    payload["scores"] = payload["scores"][:5]
    with pytest.raises(ValidationError, match="exactly once"):
        EngineInput.model_validate(payload)
    payload["scores"] = [*payload["scores"], payload["scores"][0]]
    with pytest.raises(ValidationError, match="exactly once"):
        EngineInput.model_validate(payload)
