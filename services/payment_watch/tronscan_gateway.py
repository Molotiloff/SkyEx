from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from services.payment_watch.models import TronTransfer


class TronscanGatewayError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class TronscanSettings:
    base_url: str = "https://apilist.tronscanapi.com"
    api_key: str | None = None
    usdt_contract: str = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


class TronscanGateway:
    def __init__(
        self,
        *,
        settings: TronscanSettings,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.settings = settings
        self.timeout = httpx.Timeout(timeout_seconds, connect=10.0)

    async def list_usdt_transfers(
        self,
        *,
        address: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int | None = None,
        limit: int = 50,
    ) -> list[TronTransfer]:
        params: dict[str, Any] = {
            "limit": min(max(int(limit), 1), 50),
            "start": 0,
            "contract_address": self.settings.usdt_contract,
            "relatedAddress": address,
            "start_timestamp": start_timestamp_ms,
            "confirm": "0",
        }
        if end_timestamp_ms is not None:
            params["end_timestamp"] = end_timestamp_ms

        data = await self._get_json("/api/token_trc20/transfers", params=params)
        rows = data.get("token_transfers")
        if not isinstance(rows, list):
            raise TronscanGatewayError("Tronscan вернул некорректный список переводов")

        transfers: list[TronTransfer] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            token_info = row.get("tokenInfo") or {}
            token_symbol = str(token_info.get("tokenAbbr") or "").upper()
            decimals = int(token_info.get("tokenDecimal") or 0)
            quant_raw = row.get("quant")
            tx_hash = str(row.get("transaction_id") or "").strip()
            from_address = str(row.get("from_address") or "").strip()
            to_address = str(row.get("to_address") or "").strip()
            block_number_raw = row.get("block") or row.get("block_number") or row.get("blockNumber")
            block_ts_raw = row.get("block_ts")
            confirmed = bool(row.get("confirmed"))
            if not tx_hash or not from_address or not to_address or block_ts_raw in (None, ""):
                continue
            try:
                amount = Decimal(str(quant_raw)) / (Decimal(10) ** decimals)
                block_ts = datetime.fromtimestamp(int(block_ts_raw) / 1000, tz=timezone.utc)
            except (InvalidOperation, ValueError, TypeError):
                continue
            confirmations = await self.get_confirmations(tx_hash=tx_hash)
            transfers.append(
                TronTransfer(
                    tx_hash=tx_hash,
                    from_address=from_address,
                    to_address=to_address,
                    amount=amount,
                    token_symbol=token_symbol,
                    block_number=(
                        int(block_number_raw)
                        if block_number_raw not in (None, "")
                        else None
                    ),
                    block_ts=block_ts,
                    confirmations=confirmations,
                    confirmed=confirmed,
                )
            )
        transfers.sort(key=lambda x: (x.block_ts, x.tx_hash))
        return transfers

    async def get_confirmations(self, *, tx_hash: str) -> int:
        data = await self._get_json("/api/transaction-info", params={"hash": tx_hash})
        try:
            return max(int(data.get("confirmations") or 0), 0)
        except (TypeError, ValueError):
            return 0

    async def _get_json(self, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.base_url.rstrip('/')}{path}"
        headers: dict[str, str] = {}
        if self.settings.api_key:
            headers["TRON-PRO-API-KEY"] = self.settings.api_key

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise TronscanGatewayError("Tronscan не ответил вовремя") from exc
        except httpx.HTTPError as exc:
            raise TronscanGatewayError("Не удалось обратиться к Tronscan") from exc

        if response.status_code >= 400:
            raise TronscanGatewayError(f"Tronscan вернул HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise TronscanGatewayError("Tronscan вернул некорректный JSON") from exc
        if not isinstance(data, dict):
            raise TronscanGatewayError("Tronscan вернул неожиданный формат ответа")
        return data
