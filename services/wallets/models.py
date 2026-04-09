from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class WalletCommandResult:
    ok: bool
    message_text: str
    reply_markup: object | None = None


@dataclass(slots=True, frozen=True)
class CityTransferResultView:
    ok: bool
    message_text: str
