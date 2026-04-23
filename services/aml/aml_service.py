from __future__ import annotations

import time

from services.aml.getblock_client import GetBlockAMLClient
from services.aml.getblock_parser import (
    build_report_message,
    extract_amlcheckup,
    parse_report_preview,
)
from services.aml.getblock_settings import GetBlockSettings


class AMLService:
    _REPORT_PARSE_ATTEMPTS = 20
    _REPORT_PARSE_DELAY_SECONDS = 3.0

    def __init__(self, *, settings: GetBlockSettings):
        self.settings = settings

    def _build_client(self) -> GetBlockAMLClient:
        return GetBlockAMLClient(
            identity=self.settings.identity,
            password=self.settings.password,
            lang=self.settings.lang,
        )

    def check_wallet(self, wallet: str) -> dict:
        client = self._build_client()
        client.login()

        create_resp = client.create_check(
            wallet=wallet,
            currency_code=self.settings.currency_code,
            token_id=self.settings.token_id,
            user_id=self.settings.user_id,
            aml_provider=self.settings.aml_provider,
            direction=self.settings.direction,
            source=self.settings.source,
            type_=self.settings.type_,
        )

        amlcheckup = create_resp.get("amlcheckup") or extract_amlcheckup(create_resp)
        if not amlcheckup:
            raise RuntimeError("amlcheckup не найден в ответе GetBlock")

        client.get_address_page(
            wallet=wallet,
            amlcheckup=amlcheckup,
            currency_code=self.settings.currency_code,
        )

        report_data = None
        preview_link = f"{client.BASE}/{self.settings.lang}/report-preview/{amlcheckup}"
        last_parse_error: Exception | None = None
        for attempt in range(1, self._REPORT_PARSE_ATTEMPTS + 1):
            preview_html = client.get_report_preview_html(amlcheckup=amlcheckup, max_attempts=1)
            try:
                report_data = parse_report_preview(
                    preview_html,
                    amlcheckup,
                    base_url=client.BASE,
                    lang=self.settings.lang,
                )
                break
            except ValueError as e:
                last_parse_error = e
                if attempt == self._REPORT_PARSE_ATTEMPTS:
                    break
                time.sleep(self._REPORT_PARSE_DELAY_SECONDS)

        if report_data is None:
            raise RuntimeError(
                f"{last_parse_error}. Preview: {preview_link}. "
                f"Отчет не стал готовым за {self._REPORT_PARSE_ATTEMPTS * self._REPORT_PARSE_DELAY_SECONDS:.0f} сек."
            )

        message_text = build_report_message(report_data)

        return {
            "wallet": wallet,
            "amlcheckup": amlcheckup,
            "message_text": message_text,
            "report_data": report_data,
        }
