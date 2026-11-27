"""Recovery settings: safe defaults and hard bounds."""

import pytest
from pydantic import ValidationError

from svc_orchestrator.config import Settings


def test_recovery_defaults() -> None:
    s = Settings()
    assert (s.lease_ttl_s, s.resume_concurrency) == (60, 4)
    assert (s.recovery_interval_s, s.recovery_attempts) == (30, 5)
    assert (s.recovery_attempt_limit, s.finished_ttl_s) == (3, 86400)


@pytest.mark.parametrize(
    "override",
    [
        {"lease_ttl_s": 2},
        {"resume_concurrency": 0},
        {"resume_concurrency": 65},
        {"recovery_interval_s": -1},
        {"recovery_attempts": 0},
        {"recovery_attempts": 21},
        {"recovery_attempt_limit": 0},
        {"finished_ttl_s": -1},
    ],
)
def test_out_of_range_recovery_settings_are_refused(override: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        Settings(**override)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "override",
    [
        {"lease_ttl_s": 3},
        {"resume_concurrency": 64},
        {"recovery_interval_s": 0},
        {"recovery_attempt_limit": 1},
    ],
)
def test_the_edges_of_the_recovery_settings_are_accepted(override: dict[str, float]) -> None:
    Settings(**override)  # type: ignore[arg-type]


def test_a_service_cannot_be_built_with_a_zero_concurrency_or_page() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from svc_orchestrator.services.runs import RunService
    from svc_orchestrator.testing import Rig

    rig = Rig()
    for bad in (
        {"resume_concurrency": 0},
        {"recovery_page_size": 0},
        {"heartbeat_s": 0},
        {"recovery_attempt_limit": 0},
        {"lease_failures_allowed": 0},
    ):
        with pytest.raises(ValueError):
            RunService(
                context_factory=rig.context,
                checkpointer=MemorySaver(),
                config=rig.config,
                **bad,  # type: ignore[arg-type]
            )
