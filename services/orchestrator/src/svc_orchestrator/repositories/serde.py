"""Checkpoint serializer with a strict msgpack allow-list (docs/harness/07, 13).

LangGraph's default accepts any type it finds in a checkpoint (and warns). A checkpoint row is
written by M3 only, so every type that can appear in one is known: the state models and their enum.
Anything else is refused on load instead of imported, so a tampered row cannot instantiate an
arbitrary class. Add a new state model or enum here, or ``test_orch_serde`` fails.
"""

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from ..models.challenge_state import ChallengeState
from ..models.discover_state import DiscoverState
from ..models.state import RunStatus, ValidateState

STATE_TYPES: tuple[type, ...] = (RunStatus, ValidateState, DiscoverState, ChallengeState)


def checkpoint_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=STATE_TYPES)
