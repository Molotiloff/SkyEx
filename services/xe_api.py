from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx


class ConverterAPIError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class XEConvertResult:
    from_currency: str
    to_currency: str
    amount: Decimal
    rate: Decimal
    converted: Decimal
    final_amount: Decimal
    percent: Decimal | None
    percent_mode: str
    sign: int
    image_url: str | None = None

    @property
    def is_markup(self) -> bool:
        return self.percent_mode == "%%"


def _to_decimal(value: Any, *, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ConverterAPIError(f"Некорректное поле {field_name} в ответе converter API") from exc


class ConverterAPIService:
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = httpx.Timeout(timeout_seconds, connect=10.0)

    async def convert_text(self, *, text: str, include_image: bool = True) -> XEConvertResult:
        payload = {
            "text": text,
            "include_image": include_image,
        }
        data = await self._post_json("/api/v1/convert", payload)
        return self._parse_convert_response(data)

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ConverterAPIError("Converter API не ответил вовремя") from exc
        except httpx.HTTPError as exc:
            raise ConverterAPIError("Не удалось обратиться к Converter API") from exc

        data: dict[str, Any] | None = None
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            data = None

        if response.status_code >= 400:
            message = None
            if data:
                error = data.get("error")
                if isinstance(error, dict):
                    message = error.get("message")
            if response.status_code == 401:
                raise ConverterAPIError("Converter API отклонил токен авторизации")
            if response.status_code == 429:
                raise ConverterAPIError(message or "Converter API временно ограничил запросы")
            raise ConverterAPIError(message or f"Converter API вернул HTTP {response.status_code}")

        if not data:
            raise ConverterAPIError("Converter API вернул пустой или некорректный JSON")
        return data

    def _parse_convert_response(self, data: dict[str, Any]) -> XEConvertResult:
        from_currency = str(data.get("from_currency") or "").strip().upper()
        to_currency = str(data.get("to_currency") or "").strip().upper()
        if not from_currency or not to_currency:
            raise ConverterAPIError("В ответе converter API не хватает кодов валют")

        return XEConvertResult(
            from_currency=from_currency,
            to_currency=to_currency,
            amount=_to_decimal(data.get("amount"), field_name="amount"),
            rate=_to_decimal(data.get("rate"), field_name="rate"),
            converted=_to_decimal(data.get("converted"), field_name="converted"),
            final_amount=_to_decimal(data.get("final_amount"), field_name="final_amount"),
            percent=(
                _to_decimal(data.get("percent"), field_name="percent")
                if data.get("percent") not in (None, "")
                else None
            ),
            percent_mode=str(data.get("percent_mode") or "%"),
            sign=int(data.get("sign") or -1),
            image_url=(str(data.get("image_url")).strip() if data.get("image_url") else None),
        )
