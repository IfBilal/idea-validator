"""Checkpoints round-trip through a strict msgpack allow-list (no type is loaded on trust)."""

import enum
import inspect
from typing import Any

import pytest
from pydantic import BaseModel

from svc_orchestrator.models import challenge_state, discover_state, state
from svc_orchestrator.models.challenge_state import ChallengeState
from svc_orchestrator.models.discover_state import DiscoverState
from svc_orchestrator.models.state import RunStatus, ValidateState
from svc_orchestrator.repositories.serde import STATE_TYPES, checkpoint_serde

SAMPLES: dict[type, Any] = {
    RunStatus: RunStatus.AWAITING_APPROVAL,
    ValidateState: ValidateState(
        run_id="r1",
        user_id="u1",
        idea_id="i1",
        idea_text="x",
        status=RunStatus.RUNNING,
        notes=["a"],
        answers={"hours": 12.5, "other": None},
        memo={"k": {"v": [1, 2]}},
    ),
    DiscoverState: DiscoverState(run_id="r1", user_id="u1", niche="devtools", count=3),
    ChallengeState: ChallengeState(
        run_id="r1", user_id="u1", idea_id="i1", version=1, claim_id="c"
    ),
}


def declared_types() -> set[type]:
    found: set[type] = set()
    for module in (state, discover_state, challenge_state):
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__ and issubclass(cls, BaseModel | enum.Enum):
                found.add(cls)
    return found


def test_every_state_type_declared_in_the_state_modules_is_registered() -> None:
    assert declared_types() <= set(STATE_TYPES)


def test_every_registered_type_has_a_round_trip_sample() -> None:
    assert set(STATE_TYPES) == set(SAMPLES)


@pytest.mark.parametrize("cls", list(SAMPLES), ids=lambda c: c.__name__)
def test_each_state_type_round_trips_under_the_strict_allow_list(cls: type) -> None:
    serde = checkpoint_serde()
    value = SAMPLES[cls]
    restored = serde.loads_typed(serde.dumps_typed(value))
    assert restored == value
    assert type(restored) is cls


@pytest.mark.parametrize("status", list(RunStatus))
def test_every_run_status_round_trips_alone_and_inside_a_container(status: RunStatus) -> None:
    serde = checkpoint_serde()
    assert serde.loads_typed(serde.dumps_typed(status)) is status
    nested = {"s": [status], "t": {"u": status}}
    assert serde.loads_typed(serde.dumps_typed(nested)) == nested


@pytest.mark.parametrize("status", list(RunStatus))
def test_a_state_in_every_status_survives_the_strict_serializer(status: RunStatus) -> None:
    serde = checkpoint_serde()
    value = SAMPLES[ValidateState].model_copy(update={"status": status})
    assert serde.loads_typed(serde.dumps_typed(value)).status is status


def test_a_type_that_is_not_registered_is_blocked_not_loaded() -> None:
    class Unregistered(BaseModel):
        n: int

    Unregistered.__module__ = "svc_orchestrator.models.state"  # importable name, not allow-listed
    serde = checkpoint_serde()
    blob = serde.dumps_typed(Unregistered(n=1))
    assert not isinstance(serde.loads_typed(blob), Unregistered)


def test_the_serializer_is_not_the_permissive_default() -> None:
    assert checkpoint_serde()._allowed_msgpack_modules not in (True, None)
