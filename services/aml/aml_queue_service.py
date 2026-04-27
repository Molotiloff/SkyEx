from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from services.aml.aml_service import AMLService

log = logging.getLogger("aml_queue")


@dataclass(slots=True)
class AMLQueueTask:
    wallet: str
    on_success: Callable[[dict], Awaitable[None]]
    on_error: Callable[[Exception], Awaitable[None]]


class AMLQueueService:
    def __init__(self, *, aml_service: AMLService) -> None:
        self.aml_service = aml_service
        self._queue: asyncio.Queue[AMLQueueTask] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._stopped = False

    async def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._stopped = False
        self._worker_task = asyncio.create_task(self._worker(), name="aml_queue_worker")
        log.info("AML queue worker started")

    async def stop(self) -> None:
        self._stopped = True
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        log.info("AML queue worker stopped")

    async def enqueue(self, task: AMLQueueTask) -> int:
        await self._queue.put(task)
        return self._queue.qsize()

    def qsize(self) -> int:
        return self._queue.qsize()

    async def _worker(self) -> None:
        while not self._stopped:
            task = await self._queue.get()
            try:
                result = await asyncio.to_thread(self.aml_service.check_wallet, task.wallet)
                await task.on_success(result)
            except Exception as e:
                log.warning("AML task failed wallet=%s err=%r", task.wallet, e)
                await task.on_error(e)
            finally:
                self._queue.task_done()
