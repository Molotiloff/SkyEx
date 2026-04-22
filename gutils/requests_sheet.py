# gutils/requests_sheet.py
from __future__ import annotations
import json, os, re
from datetime import datetime
from decimal import Decimal
from typing import Optional, Union, Any

from googleapiclient.errors import HttpError
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials


class SheetsWriteError(Exception):
    pass


_SPREADSHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")

# Карта ячеек с внутренними курсами на листе «Главная»
_DEFAULT_CELL_MAP = {
    "EUR": "Главная!E2",
    "USDT": "Главная!E8",
    "USD": "Главная!H8",
    "USDW": "Главная!H2",
}

# Кеш только для сервисных объектов (не для значений)
_cached = {
    "service": None,
    "spreadsheet_id": None,
    "creds": None,
}


def get_service_account_email() -> str:
    try:
        creds = _get_credentials()
        return getattr(creds, "service_account_email", "") or ""
    except Exception:
        return ""


def _extract_id_from_url(maybe_url_or_id: str) -> str:
    s = (maybe_url_or_id or "").strip()
    m = _SPREADSHEET_ID_RE.search(s)
    return m.group(1) if m else s


def _get_credentials() -> Any:
    if _cached["creds"]:
        return _cached["creds"]
    json_inline = (
        os.getenv("GOOGLE_CREDENTIALS_JSON")
        or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    )
    json_path = (
        os.getenv("GOOGLE_CREDENTIALS_FILE")
        or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    if json_inline:
        info = json.loads(json_inline)
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    elif json_path:
        creds = Credentials.from_service_account_file(json_path, scopes=scopes)
    else:
        raise SheetsWriteError("Не заданы креды Google.")
    _cached["creds"] = creds
    return creds


def _get_service():
    if _cached["service"]:
        return _cached["service"]
    service = build("sheets", "v4", credentials=_get_credentials(), cache_discovery=False)
    _cached["service"] = service
    return service


def _resolve_spreadsheet_id(spreadsheet: Optional[str]) -> str:
    if _cached["spreadsheet_id"]:
        return _cached["spreadsheet_id"]
    candidate = (
        spreadsheet
        or os.getenv("GOOGLE_SHEET_URL")
        or os.getenv("GOOGLE_SHEET_ID")
        or os.getenv("SPREADSHEET_URL")
        or os.getenv("SPREADSHEET_ID")
    )
    if not candidate:
        raise SheetsWriteError("Не задан spreadsheet.")
    sid = _extract_id_from_url(candidate)
    _cached["spreadsheet_id"] = sid
    return sid


# === helpers ===
def _coerce_num(x: Union[str, int, float, Decimal]) -> str:
    s = format(Decimal(x).normalize(), "f") if isinstance(x, Decimal) else str(x)
    return s.strip().replace(" ", "").replace("\u00A0", "").replace(".", ",")


def _format_dt(value: Union[str, datetime]) -> str:
    return value.strftime("%d.%m.%Y %H:%M:%S") if isinstance(value, datetime) else str(value)


def _find_next_row(service, sid: str, sheet: str) -> int:
    resp = service.spreadsheets().values().get(spreadsheetId=sid, range=f"{sheet}!A:A").execute()
    rows = resp.get("values", [])
    return len(rows) + 1 if rows else 2


def _read_main_rate_fresh(code: str, cell_map: dict[str, str] | None = None) -> Optional[Decimal]:
    """Читает актуальный курс из листа «Главная» (без кеша)."""
    if not code:
        return None
    cell_map = cell_map or _DEFAULT_CELL_MAP
    ref = cell_map.get(code.upper())
    if not ref:
        return None
    service = _get_service()
    sid = _resolve_spreadsheet_id(None)
    resp = service.spreadsheets().values().get(spreadsheetId=sid, range=ref).execute()
    vals = resp.get("values", [])
    if not vals or not vals[0]:
        return None
    raw = str(vals[0][0]).replace("\u00A0", "").replace(" ", "").replace(",", ".").strip()
    try:
        val = Decimal(raw)
        return val if val.is_finite() else None
    except Exception:
        return None


def read_main_rate(code: str, cell_map: dict[str, str] | None = None) -> Decimal:
    value = _read_main_rate_fresh(code, cell_map)
    if value is None:
        raise SheetsWriteError(f"Не найден внутренний курс для {code!r} на листе 'Главная'.")
    return value


# === Основные функции ===
def append_sale_row(
    *,
    in_currency: str,
    out_currency: str,
    in_amount: Union[str, int, float, Decimal],
    out_amount: Union[str, int, float, Decimal],
    rate: Union[str, int, float, Decimal],
    created_at: Optional[Union[str, datetime]] = None,
    spreadsheet: Optional[str] = None,
    sheet_name: str = "Продажа",
    cell_map=None,
    request_id: Optional[int | str] = None,
) -> tuple[int, Optional[Decimal]]:
    """Записывает строку на лист 'Продажа'. Столбец D — свежий курс из «Главная», столбец B — номер заявки."""
    if cell_map is None:
        cell_map = _DEFAULT_CELL_MAP

    try:
        service = _get_service()
        sid = _resolve_spreadsheet_id(spreadsheet)
        row = _find_next_row(service, sid, sheet_name)

        out_cur = (out_currency or "").strip().upper()
        if not out_cur:
            raise SheetsWriteError("Нет валюты (out_currency).")

        val_amount = _coerce_num(out_amount)
        val_rate = _coerce_num(rate)

        # Всегда читаем актуальный курс для D
        val_input = None
        fresh = _read_main_rate_fresh(out_cur, cell_map)
        if fresh is not None:
            val_input = _coerce_num(fresh)

        data = []
        if created_at is not None:
            data.append({"range": f"{sheet_name}!A{row}", "values": [[_format_dt(created_at)]]})
        if request_id is not None:
            data.append({"range": f"{sheet_name}!B{row}", "values": [[str(request_id)]]})
        data.append({"range": f"{sheet_name}!C{row}", "values": [[out_cur]]})
        if val_input is not None:
            data.append({"range": f"{sheet_name}!D{row}", "values": [[val_input]]})
        data.extend(
            [
                {"range": f"{sheet_name}!E{row}", "values": [[val_amount]]},
                {"range": f"{sheet_name}!G{row}", "values": [[val_rate]]},
            ]
        )

        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sid, body={"valueInputOption": "USER_ENTERED", "data": data}
        ).execute()
        return row, None

    except HttpError as e:
        raise SheetsWriteError(f"Ошибка Google Sheets API: {e}") from e
    except Exception as e:
        raise SheetsWriteError(str(e)) from e


def append_buy_row(
    *,
    currency: str,
    amount: Union[str, int, float, Decimal],
    rate: Union[str, int, float, Decimal],
    created_at: Optional[Union[str, datetime]] = None,
    spreadsheet: Optional[str] = None,
    sheet_name: str = "Покупка",
    request_id: Optional[int | str] = None,
) -> int:
    """Записывает строку на лист 'Покупка'. Столбец B — номер заявки."""
    try:
        service = _get_service()
        sid = _resolve_spreadsheet_id(spreadsheet)
        row = _find_next_row(service, sid, sheet_name)

        cur = (currency or "").strip().upper()
        if not cur:
            raise SheetsWriteError("Нет валюты (currency).")

        val_amount = _coerce_num(amount)
        val_rate = _coerce_num(rate)

        data = []
        if created_at is not None:
            data.append({"range": f"{sheet_name}!A{row}", "values": [[_format_dt(created_at)]]})
        if request_id is not None:
            data.append({"range": f"{sheet_name}!B{row}", "values": [[str(request_id)]]})
        data.extend(
            [
                {"range": f"{sheet_name}!C{row}", "values": [[cur]]},
                {"range": f"{sheet_name}!D{row}", "values": [[val_amount]]},
                {"range": f"{sheet_name}!E{row}", "values": [[val_rate]]},
            ]
        )

        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sid, body={"valueInputOption": "USER_ENTERED", "data": data}
        ).execute()
        return row

    except HttpError as e:
        raise SheetsWriteError(f"Ошибка Google Sheets API: {e}") from e
    except Exception as e:
        raise SheetsWriteError(str(e)) from e

def _find_rows_by_req_id(service, sid: str, sheet_name: str, req_id: int | str) -> list[int]:
    """
    Возвращает список 0-based индексов строк, где в столбце B == req_id.
    Пропускает заголовок (если он есть).
    """
    rng = f"{sheet_name}!B:B"
    resp = service.spreadsheets().values().get(spreadsheetId=sid, range=rng).execute()
    values = resp.get("values", [])
    # values[0] -> B1; 0-based индекс для DeleteDimensionRequest = номер_строки_в_гугле - 1
    hits: list[int] = []
    needle = str(req_id).strip()
    for i, row in enumerate(values):
        cell = (row[0] if row else "").strip()
        if cell == needle:
            # i -> индекс в диапазоне B:B, т.е. это строка (i+1) в листе => 0-based row_index = i
            hits.append(i)
    # если у вас в B1 заголовок и он равен req_id — он тоже попадёт; обычно заголовок иной
    return hits


def _delete_rows_by_0based_indices(service, sid: str, sheet_id: int, rows_0based: list[int]) -> None:
    """
    Удаляет строки по их 0-based индексам через batchUpdate DeleteDimensionRequest.
    Передавайте перечисление индексов В УБЫВАЮЩЕМ порядке.
    """
    if not rows_0based:
        return
    requests = []
    for r in rows_0based:
        requests.append({
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": r,
                    "endIndex": r + 1,
                }
            }
        })
    service.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": requests}).execute()


def _get_sheet_id(service, sid: str, sheet_name: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=sid).execute()
    for sh in meta.get("sheets", []):
        props = sh.get("properties", {})
        if props.get("title") == sheet_name:
            return int(props["sheetId"])
    raise SheetsWriteError(f"Не найден лист: {sheet_name}")


def delete_rows_by_request_id(
    *,
    req_id: int | str,
    spreadsheet: Optional[str] = None,
    sheets: tuple[str, str] = ("Покупка", "Продажа"),
) -> dict[str, int]:
    """
    Удаляет ВСЕ строки, где B == req_id, на указанных листах.
    Возвращает словарь: {имя_листа: кол-во_удалённых}.
    """
    try:
        service = _get_service()
        sid = _resolve_spreadsheet_id(spreadsheet)
        results: dict[str, int] = {}
        for sheet_name in sheets:
            sheet_id = _get_sheet_id(service, sid, sheet_name)
            rows = _find_rows_by_req_id(service, sid, sheet_name, req_id)
            if rows:
                # удаляем снизу вверх
                rows_sorted = sorted(rows, reverse=True)
                _delete_rows_by_0based_indices(service, sid, sheet_id, rows_sorted)
            results[sheet_name] = len(rows or [])
        return results
    except HttpError as e:
        raise SheetsWriteError(f"Ошибка Google Sheets API: {e}") from e
    except Exception as e:
        raise SheetsWriteError(str(e)) from e
