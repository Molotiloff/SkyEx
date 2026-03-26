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

    async def _run(self) -> None:
        while not self._stopped:
            try:
                async with websockets.connect(
                    WS_URL,
                    origin="https://grinex.io",
                    ssl=self._ssl_context,
                    ping_interval=20,
                    ping_timeout=20,
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
                        if not asks:
                            continue

                        try:
                            best_ask = Decimal(str(asks[0]["price"]))
                        except Exception:
                            continue

                        if self.best_ask == best_ask:
                            continue

                        self.best_ask = best_ask
                        log.info("Grinex best ask updated: %s", best_ask)

                        await self._notify_best_ask(best_ask)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Grinex websocket error: %r", e)
                await asyncio.sleep(3)