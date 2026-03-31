from __future__ import annotations

import asyncio
import inspect
import json
import logging
import ssl
from decimal import Decimal
from typing import Awaitable, Callable

import certifi
import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

log = logging.getLogger("grinex_ws")

WS_URL = (
    "wss://ws.grinex.io/"
    "?stream=global"
    "&stream=usdta7a5"
    "&stream=trading_ui_order_book"
    "&stream=ext_markets"
    "&stream=order"
    "&stream=trade"
    "&stream=member_balance"
    "&stream=exchanger"
)


class GrinexWsService:
    def __init__(
        self,
        *,
        on_best_ask: Callable[[Decimal], Awaitable[None] | None] | None = None,
    ) -> None:
        self.on_best_ask = on_best_ask
        self.best_ask: Decimal | None = None
        self.asks: list[dict] = []
        self.bids: list[dict] = []
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run(), name="grinex_ws_service")

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _notify_best_ask(self, best_ask: Decimal) -> None:
        cb = self.on_best_ask
        if cb is None:
            return

        try:
            result = cb(best_ask)
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            log.warning("Grinex on_best_ask callback error: %r", e)

    def get_asks(self) -> list[dict]:
        return list(self.asks)

    def get_bids(self) -> list[dict]:
        return list(self.bids)

    def get_best_bid(self) -> Decimal | None:
        if not self.bids:
            return None
        try:
            return Decimal(str(self.bids[0]["price"]))
        except Exception:
            return None

    async def _run(self) -> None:
        reconnect_delay = 3

        while not self._stopped:
            try:
                log.info("Grinex websocket connecting")
                async with websockets.connect(
                    WS_URL,
                    origin="https://grinex.io",
                    ssl=self._ssl_context,
                    ping_interval=30,
                    ping_timeout=60,
                    close_timeout=10,
                    max_size=2 * 1024 * 1024,
                ) as ws:
                    log.info("Grinex websocket connected")

                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                        except Exception:
                            continue

                        book = data.get("usdta7a5.orderbook")
                        if not book:
                            continue

                        asks = book.get("ask") or []
                        bids = book.get("bid") or []

                        self.asks = asks
                        self.bids = bids

                        if not asks:
                            continue

                        try:
                            best_ask = Decimal(str(asks[0]["price"]))
                        except Exception:
                            continue

                        if self.best_ask == best_ask:
                            continue

                        self.best_ask = best_ask
                        await self._notify_best_ask(best_ask)

            except asyncio.CancelledError:
                raise
            except ConnectionClosedOK:
                log.info("Grinex websocket closed normally")
                if not self._stopped:
                    log.info("Grinex websocket reconnecting in %s sec", reconnect_delay)
                    await asyncio.sleep(reconnect_delay)
            except ConnectionClosedError as e:
                log.warning("Grinex websocket connection lost: %r", e)
                if not self._stopped:
                    log.info("Grinex websocket reconnecting in %s sec", reconnect_delay)
                    await asyncio.sleep(reconnect_delay)
            except Exception as e:
                log.warning("Grinex websocket error: %r", e)
                if not self._stopped:
                    log.info("Grinex websocket reconnecting in %s sec", reconnect_delay)
                    await asyncio.sleep(reconnect_delay)