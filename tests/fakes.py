"""Test doubles."""

from __future__ import annotations

from typing import Any


class FakeClient:
    """Stands in for the counterparty service: records TCRs instead of doing HTTP."""

    def __init__(self, counterparties: tuple[str, ...]) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.modes = {cp: "ACK" for cp in counterparties}
        self.unreachable: set[str] = set()

    def send_tcr(self, counterparty: str, tcr: dict[str, Any]) -> None:
        if counterparty in self.unreachable:
            raise ConnectionError(f"{counterparty} is down")
        self.sent.append((counterparty, tcr))

    def get_modes(self) -> dict[str, str]:
        return dict(self.modes)

    def set_mode(self, counterparty: str, mode: str) -> dict[str, str]:
        self.modes[counterparty] = mode
        return {"code": counterparty, "mode": mode}

    def reports_for(self, counterparty: str) -> list[dict[str, Any]]:
        return [t for cp, t in self.sent if cp == counterparty]
