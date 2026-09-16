from __future__ import annotations

import asyncio
import gc
import tracemalloc

import pytest

from services.aml import getblock_parser
from services.aml.aml_queue_service import (
    AMLQueueFullError,
    AMLQueueService,
    AMLQueueTask,
)
from services.aml.aml_service import AMLService
from services.aml.getblock_settings import GetBlockSettings


def _settings() -> GetBlockSettings:
    return GetBlockSettings(
        identity="identity",
        password="password",
        lang="en",
        user_id="1",
        currency_code="TRX",
        token_id="USDT",
        aml_provider="provider",
        direction="deposit",
        source="source",
        type_="address",
        reports_dir="reports",
    )


def _ready_report_html() -> str:
    return """
    <html><body><div id="report-info">
      <div class="details-info-item"><p>Blockchain: <span>TRON</span></p></div>
      <div class="details-info-item"><p>Token: <span>USDT</span></p></div>
      <div class="details-info-item"><p>Hash: <span>TXyz</span></p></div>
      <p class="risk-level"><span>12%</span><span>Low risk level</span></p>
    </div></body></html>
    """


def test_report_parser_releases_soup_without_waiting_for_cyclic_gc() -> None:
    filler = '<div><span>payload</span><a href="#">link</a></div>' * 200
    report_html = _ready_report_html().replace("<html><body>", f"<html><body>{filler}")
    gc.collect()
    gc.disable()
    tracemalloc.start()
    try:
        for index in range(5):
            result = getblock_parser.parse_report_preview(
                report_html,
                str(index),
                base_url="https://example.test",
                lang="en",
            )
            assert result["risk_percent"] == "12%"

        retained_bytes, _peak_bytes = tracemalloc.get_traced_memory()
        assert retained_bytes < 2 * 1024 * 1024
    finally:
        tracemalloc.stop()
        gc.enable()
        gc.collect()


def test_csrf_parser_uses_lightweight_path_for_standard_markup(monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("BeautifulSoup must not be used for standard CSRF markup")

    monkeypatch.setattr(getblock_parser, "BeautifulSoup", fail_if_called)

    assert (
        getblock_parser.extract_csrf_from_html(
            '<meta name="csrf-token" content="header-token">'
        )
        == "header-token"
    )
    assert (
        getblock_parser.find_hidden_csrf_field(
            '<input type="hidden" name="_csrf" value="form-token">'
        )
        == "form-token"
    )


def test_aml_service_closes_client_after_success(monkeypatch) -> None:
    class FakeClient:
        BASE = "https://example.test"

        def __init__(self) -> None:
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            self.closed = True

        def login(self) -> None:
            pass

        def create_check(self, **kwargs) -> dict:
            return {"amlcheckup": "check-id"}

        def get_address_page(self, **kwargs) -> dict:
            return {}

        def get_report_preview_html(self, **kwargs) -> str:
            return _ready_report_html()

    client = FakeClient()
    service = AMLService(settings=_settings())
    monkeypatch.setattr(service, "_build_client", lambda: client)

    result = service.check_wallet("TXyz")

    assert result["amlcheckup"] == "check-id"
    assert client.closed is True


def test_aml_queue_rejects_tasks_when_full() -> None:
    async def callback(_value) -> None:
        pass

    async def scenario() -> None:
        queue = AMLQueueService(aml_service=object(), max_queue_size=1)  # type: ignore[arg-type]
        task = AMLQueueTask(wallet="wallet", on_success=callback, on_error=callback)
        assert await queue.enqueue(task) == 1
        with pytest.raises(AMLQueueFullError):
            await queue.enqueue(task)

    asyncio.run(scenario())
