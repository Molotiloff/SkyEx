# gutils/sheets.py
from __future__ import annotations

import csv
import json
import os
import re
import ssl
import urllib.request
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Optional, Tuple
from urllib.parse import quote

import certifi

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None


class SheetsNotConfigured(RuntimeError): ...


class SheetsReadError(RuntimeError): ...


_THOUSAND_SEPS = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}

# <<< НОВОЕ: карта валют -> A1-ячейки на листе «Главная» >>>
# Можно переопределить через JSON в GOOGLE_BALANCE_CELLS_JSON
_DEFAULT_CELL_MAP = {
    "EUR": "Главная!E1",
    "USDT": "Главная!E7",  # tether
    "USD": "Главная!H7",
    "USDW": "Главная!H1",
}
# Доп. алиасы (нормализация входных кодов)
_ALIASES = {
    "TETHER": "USDT",
}


def _norm_code(code: str) -> str:
    c = (code or "").strip().upper()
    return _ALIASES.get(c, c)


def _to_decimal(s: str) -> Decimal:
    x = s
    for ch in _THOUSAND_SEPS:
        x = x.replace(ch, "")
    x = x.replace(",", ".").strip()
    if x == "":
        return Decimal("0")
    try:
        return Decimal(x)
    except InvalidOperation as e:
        raise SheetsReadError(f"Не число в таблице: {s!r}") from e


# ---------- Service Account ----------
def _sa_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    json_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not raw_json and not json_path:
        return None
    if gspread is None or Credentials is None:
        raise SheetsNotConfigured("Установите gspread и google-auth для SA-режима")
    if raw_json:
        info = json.loads(raw_json)
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(json_path, scopes=scopes)
    return gspread.authorize(creds)


# ---------- Публичный доступ (CSV) ----------
_SHEET_URL_RE = re.compile(r"https://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)", re.I)


def _parse_sheet_url(url: str) -> Tuple[str, Optional[str]]:
    m = _SHEET_URL_RE.search(url or "")
    if not m:
        raise SheetsNotConfigured("Некорректная ссылка на Google Sheet")
    sheet_id = m.group(1)
    gid = None
    m_gid = re.search(r"[?&#]gid=(\d+)", url)
    if m_gid:
        gid = m_gid.group(1)
    return sheet_id, gid


def _csv_export_url(sheet_id: str, gid: Optional[str], *, range_a1: Optional[str] = None) -> str:
    """
    https://docs.google.com/spreadsheets/d/<ID>/export?format=csv[&gid=...][&range=...]
    Если указываем range, лучше НЕ указывать gid (гугл иногда ругается).
    range A1 кодируем percent-encoding (лист может быть 'Главная').
    """
    base = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    if range_a1:
        encoded = quote(range_a1, safe="!:$,._-")
        return f"{base}&range={encoded}"
    if gid:
        return f"{base}&gid={gid}"
    return base


def _http_get_text(url: str, timeout: float = 10.0) -> str:
    ctx = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = resp.read()
            return data.decode("utf-8", errors="replace")
    except Exception as e:
        # второй шанс без кастомного контекста (редкие MITM/корп. прокси)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                return data.decode("utf-8", errors="replace")
        except Exception:
            raise SheetsReadError(f"Не удалось скачать CSV: {e}") from e


def _split_a1(a1: str) -> tuple[str, str]:
    """Разбивает 'Лист!E7' -> ('Лист', 'E7'). Если без '!' — ('', 'E7')."""
    a1 = (a1 or "").strip()
    if "!" in a1:
        sheet, cell = a1.split("!", 1)
        return sheet, cell
    return "", a1


def _gviz_single_cell_url(sheet_id: str, sheet_name: str, cell: str) -> str:
    # https://docs.google.com/spreadsheets/d/<ID>/gviz/tq?tqx=out:csv&sheet=<sheet>&range=<cell>
    base = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
    return f"{base}&sheet={quote(sheet_name, safe='')}&range={quote(cell, safe=':$,._-')}"


def _iter_csv(text: str):
    f = StringIO(text)
    reader = csv.reader(f)
    for row in reader:
        yield row


# ---------- Чтение одной ячейки ----------
def _get_cell_map() -> dict[str, str]:
    raw = os.getenv("GOOGLE_BALANCE_CELLS_JSON", "").strip()
    if not raw:
        return dict(_DEFAULT_CELL_MAP)
    try:
        m = json.loads(raw)
        # нормализуем ключи к UPPER
        return {_norm_code(k): str(v) for k, v in m.items()}
    except Exception as e:
        raise SheetsReadError(f"Некорректный GOOGLE_BALANCE_CELLS_JSON: {e}")


def _read_single_cell_sa(sheet_id: str, a1: str) -> str:
    client = _sa_client()
    if not client:
        raise SheetsNotConfigured("SA не настроен")
    try:
        sh = client.open_by_key(sheet_id)
        # gspread умеет ws.acell("A1"), но нам удобнее через get(a1) -> [[value]]
        values = sh.values_get(a1).get("values", [])
        if not values or not values[0]:
            return ""
        return str(values[0][0])
    except Exception as e:
        raise SheetsReadError(f"Ошибка чтения ячейки {a1} (SA): {e}") from e


def _read_single_cell_public(sheet_url: str, a1: str) -> str:
    sheet_id, gid_from_url = _parse_sheet_url(sheet_url)
    gid_env = os.getenv("GOOGLE_SHEET_GID", "").strip()
    gid = gid_env or gid_from_url  # если указан в .env — берём его приоритетно

    sheet_name, cell = _split_a1(a1)

    # Try #1: export?format=csv&range=Лист!E7 (без gid)
    try:
        url1 = _csv_export_url(sheet_id, gid=None, range_a1=a1)
        text1 = _http_get_text(url1)
        for row in _iter_csv(text1):
            if row:
                return str(row[0])
    except Exception:
        pass  # пойдём дальше

    # Try #2: export?format=csv&gid=<gid>&range=E7 (без имени листа)
    if gid and cell:
        try:
            url2 = _csv_export_url(sheet_id, gid=gid, range_a1=None) + f"&range={quote(cell, safe=':$,._-')}"
            text2 = _http_get_text(url2)
            for row in _iter_csv(text2):
                if row:
                    return str(row[0])
        except Exception:
            pass

    # Try #3: gviz/tq?tqx=out:csv&sheet=<Лист>&range=E7
    if sheet_name and cell:
        try:
            url3 = _gviz_single_cell_url(sheet_id, sheet_name, cell)
            text3 = _http_get_text(url3)
            for row in _iter_csv(text3):
                if row:
                    return str(row[0])
        except Exception:
            pass

    raise SheetsReadError(
        "CSV экспорт одной ячейки не удался (range/gid/gviz). "
        "Укажи GOOGLE_SHEET_GID для нужного листа или включи Service Account."
    )


# ---------- Публичный API ----------
def get_firm_balance(currency_code: str) -> Decimal:
    """
    Возвращает остаток фирмы по валюте (Decimal).
    Приоритеты:
      1) Если код есть в карте валют->ячейки (по умолчанию «Главная» E1/E7/H7/H1),
         читаем ЭТУ ячейку (SA-режим или публичная ссылка).
      2) Иначе пытаемся read-only таблицу CODE|AMOUNT (как раньше).
    Переменные:
      SA-режим: GOOGLE_SERVICE_ACCOUNT_FILE/JSON + GOOGLE_SHEET_ID
      Публично: GOOGLE_SHEET_URL (любой доступ по ссылке), опц. GOOGLE_SHEET_GID
      Карта ячеек: опц. GOOGLE_BALANCE_CELLS_JSON ({"USDT":"Главная!E7", ...})
      Фолбэк-диапазон: GOOGLE_BALANCE_RANGE (по умолчанию Balances!A:B)
    """
    code = _norm_code(currency_code)

    cell_map = _get_cell_map()
    a1 = cell_map.get(code)

    # 1) если задана ячейка для кода — читаем её
    if a1:
        sa = _sa_client()
        sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
        url = os.getenv("GOOGLE_SHEET_URL", "").strip()
        raw_val = ""
        if sa and sheet_id:
            raw_val = _read_single_cell_sa(sheet_id, a1)
        elif url:
            raw_val = _read_single_cell_public(url, a1)
        else:
            raise SheetsNotConfigured("Не настроен ни SA, ни публичная ссылка для чтения ячейки")

        return _to_decimal(raw_val)

    # 2) фолбэк: читаем таблицу CODE|AMOUNT
    sa = _sa_client()
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    if sa and sheet_id:
        rng = os.getenv("GOOGLE_BALANCE_RANGE", "Balances!A:B")
        try:
            sh = sa.open_by_key(sheet_id)
            ws_name, _ = rng.split("!", 1)
            ws = sh.worksheet(ws_name)
            values = ws.get(rng)
        except Exception as e:
            raise SheetsReadError(f"Ошибка чтения SA-режима: {e}") from e

        for row in values:
            if not row:
                continue
            c0 = _norm_code(str(row[0]))
            if c0 == code:
                amount_cell = str(row[1]) if len(row) > 1 else "0"
                return _to_decimal(amount_cell)
        return Decimal("0")

    url = os.getenv("GOOGLE_SHEET_URL", "").strip()
    if not url:
        raise SheetsNotConfigured("Не настроен ни SA-режим, ни GOOGLE_SHEET_URL")

    # по публичной ссылке — если есть GID и нет RANGE, читаем весь лист; иначе можно указать A1-диапазон
    sheet_id2, gid = _parse_sheet_url(url)
    gid_env = os.getenv("GOOGLE_SHEET_GID", "").strip() or gid
    range_a1 = os.getenv("GOOGLE_BALANCE_RANGE", "").strip() or None
    csv_url = _csv_export_url(sheet_id2, gid_env or None, range_a1=range_a1)
    text = _http_get_text(csv_url)
    for row in _iter_csv(text):
        if not row:
            continue
        c0 = _norm_code(str(row[0]))
        if c0 == code:
            amount_cell = str(row[1]) if len(row) > 1 else "0"
            return _to_decimal(amount_cell)
    return Decimal("0")
