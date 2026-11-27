"""Builds the typed module clients from service URLs and the per-run-kind config."""

from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from common.http import ServiceClient

from ..services.run_config import RunConfig
from .analyst import AnalystClient
from .critic import CriticClient
from .kb import KbClient
from .profile import ProfileClient
from .scouts import ScoutsClient
from .validators import ValidatorsClient
from .verdict import VerdictClient

SERVICES = ("profile", "kb", "analyst", "scouts", "validators", "verdict", "critic")


@dataclass(frozen=True)
class Clients:
    profile: ProfileClient
    kb: KbClient
    analyst: AnalystClient
    scouts: ScoutsClient
    validators: ValidatorsClient
    verdict: VerdictClient
    critic: CriticClient
    _http: tuple[ServiceClient, ...]

    async def aclose(self) -> None:
        for http in self._http:
            await http.aclose()


def build_clients(
    urls: Mapping[str, str],
    config: RunConfig,
    *,
    internal_token: str | None,
    backoff_s: float = 0.2,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Clients:
    """One ``ServiceClient`` per target, each with the timeout the workflow table gives it."""
    created: list[ServiceClient] = []

    def make(service: str, timeout_s: float) -> ServiceClient:
        client = ServiceClient(
            urls[service],
            timeout_s=timeout_s,
            max_retries=config.max_retries,
            internal_token=internal_token,
            backoff_s=backoff_s,
            transport=transport,
        )
        created.append(client)
        return client

    t = config.timeouts
    return Clients(
        profile=ProfileClient(make("profile", t.profile), make("profile", t.cache)),
        kb=KbClient(make("kb", t.kb_search)),
        analyst=AnalystClient(make("analyst", t.features)),
        scouts=ScoutsClient(make("scouts", t.scout)),
        validators=ValidatorsClient(make("validators", t.scorer)),
        verdict=VerdictClient(make("verdict", t.verdict)),
        critic=CriticClient(make("critic", max(t.critic, t.approval))),
        _http=tuple(created),
    )
