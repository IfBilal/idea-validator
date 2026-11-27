"""Everything one run needs, bound once: config, budget ledger, tracer, events and clients."""

from dataclasses import dataclass, field

from common.budget import BudgetLedger
from common.tracing import Tracer

from ..clients.factory import Clients
from ..repositories.events import RunEmitter
from .run_config import RunConfig
from .steps import PROCESS_ID, StepRunner

VERDICT_CACHE_TTL_S = 24 * 3600  # docs/harness/04 §3


@dataclass
class RunContext:
    run_id: str
    config: RunConfig
    ledger: BudgetLedger
    tracer: Tracer
    emitter: RunEmitter
    clients: Clients
    runner: StepRunner = field(init=False)
    verdict_ttl_s: int = VERDICT_CACHE_TTL_S
    owner: str = PROCESS_ID  # who claims steps in the ledger: one id per replica

    def __post_init__(self) -> None:
        self.runner = StepRunner(
            run_id=self.run_id,
            config=self.config,
            ledger=self.ledger,
            tracer=self.tracer,
            emitter=self.emitter,
            owner=self.owner,
        )
