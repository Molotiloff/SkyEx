from __future__ import annotations

from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from gutils import requests_sheet
from services.request_table.table_done_service import (
    RequestTableDoneService,
    TableDonePayload,
)


class _GoogleRequest:
    def __init__(self, response: dict) -> None:
        self.response = response

    def execute(self) -> dict:
        return self.response


class _ValuesApi:
    def __init__(self) -> None:
        self.updated_body: dict | None = None

    def get(self, *, spreadsheetId: str, range: str) -> _GoogleRequest:
        del spreadsheetId
        if range == "Продажа!A:A":
            return _GoogleRequest({"values": [["Дата"]]})
        if range == "Главная!H8":
            return _GoogleRequest({"values": [["91,25"]]})
        if range == "Главная!H2":
            return _GoogleRequest({"values": [["92,50"]]})
        return _GoogleRequest({"values": []})

    def batchUpdate(self, *, spreadsheetId: str, body: dict) -> _GoogleRequest:
        del spreadsheetId
        self.updated_body = body
        return _GoogleRequest({"totalUpdatedCells": len(body["data"])})


class _SpreadsheetsApi:
    def __init__(self, values_api: _ValuesApi) -> None:
        self.values_api = values_api

    def values(self) -> _ValuesApi:
        return self.values_api


class _SheetsService:
    def __init__(self) -> None:
        self.values_api = _ValuesApi()
        self.spreadsheets_api = _SpreadsheetsApi(self.values_api)

    def spreadsheets(self) -> _SpreadsheetsApi:
        return self.spreadsheets_api


class _TradeGateway:
    def __init__(self) -> None:
        self.sale_calls: list[dict] = []

    def append_sale_row(self, **kwargs) -> tuple[int, Decimal | None]:
        self.sale_calls.append(kwargs)
        return 2, Decimal("91.25")


class RequestsSheetTest(TestCase):
    def test_append_sale_row_resolves_table_currency_aliases(self) -> None:
        cases = (("USD BL", "91,25"), ("USD WH", "92,5"))

        for table_currency, expected_rate in cases:
            with self.subTest(table_currency=table_currency):
                service = _SheetsService()
                with (
                    patch.object(requests_sheet, "_get_service", return_value=service),
                    patch.object(
                        requests_sheet,
                        "_resolve_spreadsheet_id",
                        return_value="sheet-id",
                    ),
                ):
                    row, fresh_rate = requests_sheet.append_sale_row(
                        in_currency="RUB",
                        out_currency=table_currency,
                        in_amount=Decimal("1000"),
                        out_amount=Decimal("10"),
                        rate=Decimal("100"),
                        rate_currency="USD" if table_currency == "USD BL" else "USDW",
                        request_id=123,
                    )

                self.assertEqual(row, 2)
                self.assertEqual(fresh_rate, Decimal(expected_rate.replace(",", ".")))
                self.assertIsNotNone(service.values_api.updated_body)
                cells = {
                    item["range"]: item["values"][0][0]
                    for item in service.values_api.updated_body["data"]  # type: ignore[index]
                }
                self.assertEqual(cells["Продажа!C2"], table_currency)
                self.assertEqual(cells["Продажа!D2"], expected_rate)

    def test_append_sale_row_rejects_unknown_rate_currency(self) -> None:
        service = _SheetsService()
        with (
            patch.object(requests_sheet, "_get_service", return_value=service),
            patch.object(
                requests_sheet,
                "_resolve_spreadsheet_id",
                return_value="sheet-id",
            ),
            self.assertRaisesRegex(
                requests_sheet.SheetsWriteError,
                "Не найден внутренний курс",
            ),
        ):
            requests_sheet.append_sale_row(
                in_currency="RUB",
                out_currency="UNKNOWN",
                in_amount=Decimal("1000"),
                out_amount=Decimal("10"),
                rate=Decimal("100"),
            )

        self.assertIsNone(service.values_api.updated_body)


class RequestTableDoneServiceTest(TestCase):
    def test_rub_sale_passes_display_and_rate_currencies_separately(self) -> None:
        cases = (("USD", "USD BL"), ("USDW", "USD WH"))

        for out_currency, table_currency in cases:
            with self.subTest(out_currency=out_currency):
                gateway = _TradeGateway()
                service = RequestTableDoneService(sheets_gateway=gateway)  # type: ignore[arg-type]

                service.write_by_payload(
                    payload=TableDonePayload(
                        req_id=123,
                        in_cur="RUB",
                        out_cur=out_currency,
                        in_amt=Decimal("1000"),
                        out_amt=Decimal("10"),
                        rate=Decimal("100"),
                    ),
                    message_dt=None,
                )

                self.assertEqual(len(gateway.sale_calls), 1)
                self.assertEqual(gateway.sale_calls[0]["out_currency"], table_currency)
                self.assertEqual(gateway.sale_calls[0]["rate_currency"], out_currency)
