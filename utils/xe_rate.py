from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

_XE_URL_TMPL = "https://www.xe.com/currencyconverter/convert/?Amount=1&From={FROM}&To={TO}"

# Регэксп вытягивает число из og:description:
# <meta property="og:description" content="XE Currency Converter: 1 Euro = 1.0957 US Dollar">
_RE_OG_DESC = re.compile(r'property=["\']og:description["\']\s+content=["\'][^"\']*?=\s*([0-9][0-9.,]*)\s', re.I)


class XERateError(Exception):
    pass


async def fetch_xe_rate(from_code: str = "EUR", to_code: str = "USD", *, timeout_s: float = 10.0) -> Decimal:
    """
    Возвращает Decimal-курс из XE для Amount=1 (1 FROM = rate TO).
    Бросает XERateError при проблемах.
    """
    from_code = (from_code or "").strip().upper()
    to_code = (to_code or "").strip().upper()
    if not from_code or not to_code:
        raise XERateError("Не заданы коды валют.")

    url = _XE_URL_TMPL.format(FROM=from_code, TO=to_code)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s, headers=headers, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise XERateError(f"HTTP {resp.status_code}")
            html = resp.text
    except Exception as e:
        raise XERateError(f"Сеть/HTTP: {e}") from e

    m = _RE_OG_DESC.search(html)
    if not m:
        # запасной вариант — часто на странице есть «1 EUR = 1.09 USD» в тексте
        alt = re.search(r'\b1\s+[A-Z]{3}\s*=\s*([0-9][0-9.,]*)\s+[A-Z]{3}\b', html)
        if alt:
            num = alt.group(1)
        else:
            raise XERateError("Не удалось распарсить курс со страницы XE.")
    else:
        num = m.group(1)

    # нормализуем число: убираем пробельные/разделители тысяч, точку оставляем как десятичную
    num_norm = num.replace("\u00A0", "").replace(" ", "").replace(",", "")
    try:
        return Decimal(num_norm)
    except (InvalidOperation, ValueError) as e:
        raise XERateError(f"Некорректное число: {num}") from e