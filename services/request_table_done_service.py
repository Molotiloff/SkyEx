from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from gutils.requests_sheet import (
    SheetsWriteError,
    append_buy_row,
    append_sale_row,
    read_main_rate,
)


@dataclass(slots=True, frozen=True)
class TableDonePayload:
    req_id: int | None
    in_cur: str
    out_cur: str
    in_amt: Decimal
    out_amt: Decimal
    rate: Decimal


@dataclass(slots=True, frozen=True)
class TableDoneResult:
    sheet_type: str
    in_cur: str
    out_cur: str
    in_amt: Decimal
    out_amt: Decimal
    rate: Decimal


class RequestTableDoneService:
    _DEFAULT_CELL_MAP = {
        "EUR": "Главная!E2",
        "USDT": "Главная!E8",
        "USD": "Главная!H8",
        "USDW": "Главная!H2",
    }

    _TABLE_CURRENCY_NAMES = {
        "USD": "USD BL",
        "USDW": "USD WH",
        "EUR": "EUR",
        "USDT": "USDT",
    }

    _SEP = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}

    @classmethod
    def _to_decimal(cls, raw: str) -> Decimal:
        value = (raw or "").strip().replace(",", ".")
        for ch in cls._SEP:
            value = value.replace(ch, "")
        return Decimal(value)

    @classmethod
    def parse_callback_payload(cls, data: str) -> TableDonePayload | None:
        parts = (data or "").split(":")
        try:
            if len(parts) >= 8 and parts[0] == "req" and parts[1] == "table_done":
                return TableDonePayload(
                    req_id=int(parts[2]),
                    in_cur=parts[3].strip().upper(),
                    out_cur=parts[4].strip().upper(),
                    in_amt=cls._to_decimal(parts[5]),
                    out_amt=cls._to_decimal(parts[6]),
                    rate=cls._to_decimal(parts[7]),
                )
            if len(parts) >= 7 and parts[0] == "req" and parts[1] == "table_done":
                return TableDonePayload(
                    req_id=None,
                    in_cur=parts[2].strip().upper(),
                    out_cur=parts[3].strip().upper(),
                    in_amt=cls._to_decimal(parts[4]),
                    out_amt=cls._to_decimal(parts[5]),
                    rate=cls._to_decimal(parts[6]),
                )
        except (InvalidOperation, ValueError, IndexError):
            return None
        return None

    @classmethod
    def _map_table_currency(cls, cur: str) -> str:
        return cls._TABLE_CURRENCY_NAMES.get(cur, cur)

    @staticmethod
    def _message_time(message_dt: datetime | None) -> datetime | None:
        if not isinstance(message_dt, datetime):
            return None
        if message_dt.tzinfo is None:
            message_dt = message_dt.replace(tzinfo=timezone.utc)
        return message_dt.astimezone(timezone(timedelta(hours=5)))

    def write_by_payload(
        self,
        *,
        payload: TableDonePayload,
        message_dt: datetime | None,
    ) -> TableDoneResult:
        created_at = self._message_time(message_dt)
        req_id = payload.req_id
        in_cur = payload.in_cur
        out_cur = payload.out_cur
        in_amt = payload.in_amt
        out_amt = payload.out_amt
        rate = payload.rate

        if in_cur == "USDT" and out_cur not in {"EUR", "USD", "USDW"}:
            append_buy_row(
                currency="USDT",
                amount=in_amt,
                rate=rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Покупка",
                request_id=req_id,
            )
            sheet_type = "Покупка"

        elif out_cur == "USDT" and in_cur not in {"EUR", "USD", "USDW"}:
            append_sale_row(
                in_currency=in_cur,
                out_currency="USDT",
                in_amount=in_amt,
                out_amount=out_amt,
                rate=rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Продажа",
                request_id=req_id,
            )
            sheet_type = "Продажа"

        elif out_cur in {"EUR", "USD", "USDW"} and in_cur == "RUB":
            append_sale_row(
                in_currency="RUB",
                out_currency=self._map_table_currency(out_cur),
                in_amount=in_amt,
                out_amount=out_amt,
                rate=rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Продажа",
                request_id=req_id,
            )
            sheet_type = "Продажа"

        elif in_cur in {"EUR", "USD", "USDW"} and out_cur == "RUB":
            append_buy_row(
                currency=self._map_table_currency(in_cur),
                amount=in_amt,
                rate=rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Покупка",
                request_id=req_id,
            )
            sheet_type = "Покупка"

        elif in_cur in {"EUR", "USD", "USDW"} and out_cur == "USDT":
            inner_rate = read_main_rate(in_cur, self._DEFAULT_CELL_MAP)
            rub_total = in_amt * inner_rate
            append_buy_row(
                currency=self._map_table_currency(in_cur),
                amount=in_amt,
                rate=inner_rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Покупка",
                request_id=req_id,
            )
            final_rate = rub_total / out_amt
            append_sale_row(
                in_currency=self._map_table_currency(in_cur),
                out_currency="USDT",
                in_amount=in_amt,
                out_amount=out_amt,
                rate=final_rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Продажа",
                request_id=req_id,
            )
            sheet_type = f"Покупка + Продажа ({self._map_table_currency(in_cur)})"

        elif in_cur == "USDT" and out_cur in {"EUR", "USD", "USDW"}:
            inner_rate = read_main_rate(out_cur, self._DEFAULT_CELL_MAP)
            rub_total = out_amt * inner_rate
            append_sale_row(
                in_currency="USDT",
                out_currency=self._map_table_currency(out_cur),
                in_amount=in_amt,
                out_amount=out_amt,
                rate=inner_rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Продажа",
                request_id=req_id,
            )
            final_rate = rub_total / in_amt
            append_buy_row(
                currency="USDT",
                amount=in_amt,
                rate=final_rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Покупка",
                request_id=req_id,
            )
            sheet_type = f"Продажа + Покупка ({self._map_table_currency(out_cur)})"

        elif in_cur in {"EUR", "USD", "USDW"} and out_cur in {"EUR", "USD", "USDW"}:
            in_rate = read_main_rate(in_cur, self._DEFAULT_CELL_MAP)
            rub_total = in_amt * in_rate
            append_buy_row(
                currency=self._map_table_currency(in_cur),
                amount=in_amt,
                rate=in_rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Покупка",
                request_id=req_id,
            )
            if out_amt <= 0:
                raise SheetsWriteError("Сумма продажи должна быть > 0.")
            sale_rate = rub_total / out_amt
            pretty_out = self._map_table_currency(out_cur)
            custom_cell_map = dict(self._DEFAULT_CELL_MAP)
            if pretty_out not in custom_cell_map:
                custom_cell_map[pretty_out] = self._DEFAULT_CELL_MAP[out_cur]
            append_sale_row(
                in_currency=in_cur,
                out_currency=pretty_out,
                in_amount=in_amt,
                out_amount=out_amt,
                rate=sale_rate,
                created_at=created_at,
                spreadsheet=None,
                sheet_name="Продажа",
                cell_map=custom_cell_map,
                request_id=req_id,
            )
            sheet_type = (
                f"Покупка + Продажа "
                f"({self._map_table_currency(in_cur)}→{self._map_table_currency(out_cur)})"
            )

        else:
            raise SheetsWriteError("Неизвестная пара валют. Запись не выполнена.")

        return TableDoneResult(
            sheet_type=sheet_type,
            in_cur=in_cur,
            out_cur=out_cur,
            in_amt=in_amt,
            out_amt=out_amt,
            rate=rate,
        )
