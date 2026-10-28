"""
Provider registry: builds the providers listed in ENABLED_PROVIDERS, connects
them at startup and gives the rest of the app a single place to look them up.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from app.core.config import Settings
from app.core.exceptions import DataBridgeError, ProviderNotFoundError
from app.models.market import ProviderStatus
from app.providers.base import MarketDataProvider
from app.providers.failover_provider import FailoverProvider
from app.providers.mt5_provider import MT5Provider
from app.providers.oanda_provider import OandaProvider
from app.providers.simulated_provider import SimulatedProvider

log = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._providers: Dict[str, MarketDataProvider] = {}
        for name in settings.enabled_provider_list:
            self._providers[name] = self._build(name)
        log.info("Providers enabled: %s", ", ".join(self._providers) or "<none>")

    def _build(self, name: str) -> MarketDataProvider:
        if name == "simulated":
            return SimulatedProvider(tick_interval_ms=self._settings.sim_tick_interval_ms)
        if name == "mt5":
            return MT5Provider(self._settings)
        if name == "oanda":
            return OandaProvider(self._settings)
        if name == "auto":
            # enabled_provider_list guarantees both sources exist and are built first
            return FailoverProvider(
                primary=self._providers[self._settings.failover_primary],
                fallback=self._providers[self._settings.failover_fallback],
                switch_back_delay_s=self._settings.failover_switch_back_delay_s,
            )
        raise ProviderNotFoundError(f"Unknown provider '{name}'")

    # ------------------------------------------------------------- access
    def get(self, name: str) -> MarketDataProvider:
        provider = self._providers.get(name.lower())
        if provider is None:
            raise ProviderNotFoundError(
                f"Provider '{name}' is not enabled. Enabled: {', '.join(self._providers) or 'none'}"
            )
        return provider

    def all(self) -> List[MarketDataProvider]:
        return list(self._providers.values())

    def statuses(self) -> List[ProviderStatus]:
        return [p.status() for p in self._providers.values()]

    # ---------------------------------------------------------- lifecycle
    async def connect_all(self) -> None:
        """Connect every provider. One failing provider never blocks the others."""
        for name, provider in self._providers.items():
            try:
                await provider.connect()
                log.info("Provider '%s' connected", name)
            except DataBridgeError as exc:
                log.warning("Provider '%s' failed to connect: %s", name, exc.message)
            except Exception:
                log.exception("Provider '%s' crashed while connecting", name)

    async def disconnect_all(self) -> None:
        for name, provider in self._providers.items():
            try:
                await provider.disconnect()
            except Exception:
                log.exception("Provider '%s' failed to disconnect cleanly", name)
