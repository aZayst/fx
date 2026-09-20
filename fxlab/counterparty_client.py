"""How the gateway talks to the (fake) counterparties.

`CounterpartyClient` is a Protocol so tests can swap in a fake that records
messages instead of doing network I/O (dependency injection).
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx


class CounterpartyClient(Protocol):
    def send_tcr(self, counterparty: str, tcr: dict[str, Any]) -> None: ...
    def get_modes(self) -> dict[str, str]: ...
    def set_mode(self, counterparty: str, mode: str) -> dict[str, str]: ...


class HttpCounterpartyClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 3.0,
        transport: httpx.BaseTransport | None = None,  # tests inject a mock transport
    ) -> None:
        headers = {"X-API-Key": api_key} if api_key else {}
        self._http = httpx.Client(
            base_url=base_url, headers=headers, timeout=timeout, transport=transport
        )

    def send_tcr(self, counterparty: str, tcr: dict[str, Any]) -> None:
        self._http.post(f"/tcr/{counterparty}", json=tcr).raise_for_status()

    def get_modes(self) -> dict[str, str]:
        resp = self._http.get("/agencies")
        resp.raise_for_status()
        return {a["code"]: a["mode"] for a in resp.json()}

    def set_mode(self, counterparty: str, mode: str) -> dict[str, str]:
        resp = self._http.put(f"/agencies/{counterparty}/mode", json={"mode": mode})
        resp.raise_for_status()
        return resp.json()
