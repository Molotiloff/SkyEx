from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from gutils.requests_sheet import (
    append_buy_row,
    append_sale_row,
    delete_rows_by_request_id,
    get_service_account_email,
    read_main_rate,
)
from services.request_table.sheets_trade_gateway import SheetsTradeGateway


class GutilsSheetsTradeGateway(SheetsTradeGateway):
    def get_service_account_email(self) -> str:
        return get_service_account_email()

    def read_main_rate(self, code: str, cell_map: dict[str, str] | None = None) -> Decimal:
        return read_main_rate(code, cell_map)

    def append_sale_row(
        self,
        *,
        in_currency: str,
        out_currency: str,
        in_amount: Decimal,
        out_amount: Decimal,
        rate: Decimal,
        created_at: datetime | None = None,
        spreadsheet: str | None = None,
        sheet_name: str = "Продажа",
        cell_map: dict[str, str] | None = None,
        request_id: int | str | None = None,
    ) -> tuple[int, Decimal | None]:
        return append_sale_row(
            in_currency=in_currency,
            out_currency=out_currency,
            in_amount=in_amount,
            out_amount=out_amount,
            rate=rate,
            created_at=created_at,
            spreadsheet=spreadsheet,
            sheet_name=sheet_name,
            cell_map=cell_map,
            request_id=request_id,
        )

    def append_buy_row(
        self,
        *,
        currency: str,
        amount: Decimal,
        rate: Decimal,
        created_at: datetime | None = None,
        spreadsheet: str | None = None,
        sheet_name: str = "Покупка",
        request_id: int | str | None = None,
    ) -> int:
        return append_buy_row(
            currency=currency,
            amount=amount,
            rate=rate,
            created_at=created_at,
            spreadsheet=spreadsheet,
            sheet_name=sheet_name,
            request_id=request_id,
        )

    def delete_rows_by_request_id(
        self,
        *,
        req_id: int | str,
        spreadsheet: str | None = None,
        sheets: tuple[str, str] = ("Покупка", "Продажа"),
    ) -> dict[str, int]:
        return delete_rows_by_request_id(
            req_id=req_id,
            spreadsheet=spreadsheet,
            sheets=sheets,
        )
