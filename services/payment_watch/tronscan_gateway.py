from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from services.payment_watch.models import TronTransfer

log = logging.getLogger("payment_watch")


class TronscanGatewayError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class TronscanSettings:
    base_url: str = "https://apilist.tronscanapi.com"
    api_key: str | None = None
    usdt_contract: str = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


class TronscanGateway:
    # Лимиты Tronscan с API-ключом: 5 запросов/сек на ключ, 100k запросов/день
    # на аккаунт. Интервал 0.4 c (= 2.5 req/s) держит двукратный запас по секундным
    # окнам; 429/5xx дополнительно ретраятся с экспоненциальным backoff.
    MIN_REQUEST_INTERVAL_SECONDS = 0.4
    MAX_ATTEMPTS = 4
    BACKOFF_BASE_SECONDS = 1.0
    CONFIRMATIONS_CACHE_MAX = 2000

    def __init__(
        self,
        *,
        settings: TronscanSettings,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.settings = settings
        self.timeout = httpx.Timeout(timeout_seconds, connect=10.0)
        self._client: httpx.AsyncClient | None = None
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0
        # tx_hash -> confirmations (>= 1). Подтверждённость не убывает,
        # так что повторно опрашивать /transaction-info по этим hash'ам не нужно.
        self._confirmations_cache: dict[str, int] = {}

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def list_usdt_transfers(
        self,
        *,
        address: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int | None = None,
        limit: int = 50,
        skip_confirmation_hashes: set[str] | None = None,
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

        skip_hashes = skip_confirmation_hashes or set()
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
                block_ts = datetime.fromtimestamp(int(block_ts_raw) / 1000, tz=UTC)
            except (InvalidOperation, ValueError, TypeError):
                continue
            if tx_hash in skip_hashes:
                # Уже обработанные переводы: их confirmations никто не читает,
                # лишний запрос к /transaction-info не делаем.
                confirmations = self._confirmations_cache.get(tx_hash, 0)
            else:
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
        cached = self._confirmations_cache.get(tx_hash)
        if cached is not None:
            return cached

        data = await self._get_json("/api/transaction-info", params={"hash": tx_hash})
        try:
            confirmations = max(int(data.get("confirmations") or 0), 0)
        except (TypeError, ValueError):
            return 0
        if confirmations >= 1:
            self._remember_confirmations(tx_hash, confirmations)
        return confirmations

    def _remember_confirmations(self, tx_hash: str, confirmations: int) -> None:
        if len(self._confirmations_cache) >= self.CONFIRMATIONS_CACHE_MAX:
            oldest = next(iter(self._confirmations_cache))
            self._confirmations_cache.pop(oldest, None)
        self._confirmations_cache[tx_hash] = confirmations

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def _get_json(self, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.base_url.rstrip('/')}{path}"
        headers: dict[str, str] = {}
        if self.settings.api_key:
            headers["TRON-PRO-API-KEY"] = self.settings.api_key

        last_error: str = ""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                response = await self._throttled_get(url, params=params, headers=headers)
            except httpx.TimeoutException:
                last_error = "Tronscan не ответил вовремя"
                if attempt < self.MAX_ATTEMPTS:
                    await asyncio.sleep(self._backoff_delay(attempt))
                    continue
                raise TronscanGatewayError(last_error) from None
            except httpx.HTTPError as exc:
                raise TronscanGatewayError("Не удалось обратиться к Tronscan") from exc

            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"Tronscan вернул HTTP {response.status_code}"
                if attempt < self.MAX_ATTEMPTS:
                    delay = self._retry_after_seconds(response) or self._backoff_delay(attempt)
                    log.warning(
                        "Tronscan HTTP %s (%s), повтор через %.1f c (попытка %d/%d)",
                        response.status_code, path, delay, attempt, self.MAX_ATTEMPTS,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise TronscanGatewayError(last_error)

            if response.status_code >= 400:
                raise TronscanGatewayError(f"Tronscan вернул HTTP {response.status_code}")

            try:
                data = response.json()
            except ValueError as exc:
                raise TronscanGatewayError("Tronscan вернул некорректный JSON") from exc
            if not isinstance(data, dict):
                raise TronscanGatewayError("Tronscan вернул неожиданный формат ответа")
            return data

        raise TronscanGatewayError(last_error or "Tronscan недоступен")

    async def _throttled_get(
        self,
        url: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        async with self._request_lock:
            loop = asyncio.get_running_loop()
            wait = self._last_request_at + self.MIN_REQUEST_INTERVAL_SECONDS - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self._get_client().get(url, params=params, headers=headers)
            finally:
                self._last_request_at = loop.time()

    def _backoff_delay(self, attempt: int) -> float:
        return self.BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return max(float(raw), 1.0)
        except ValueError:
            return None
