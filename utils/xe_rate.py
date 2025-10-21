from __future__ import annotations

import asyncio
import random
import re
from decimal import Decimal

import httpx

_XE_URL_TMPL = "https://www.xe.com/currencyconverter/convert/?Amount=1&From={FROM}&To={TO}"

_RE_OG_DESC = re.compile(
    r'property=["\']og:description["\']\s+content=["\'][^"\']*?=\s*([0-9][0-9.,\u00A0 ]*)\s', re.I
)
_RE_INLINE_EQ = re.compile(
    r'\b1\s+[A-Z]{3}\s*=\s*([0-9][0-9.,\u00A0 ]*)\s+[A-Z]{3}\b', re.I
)
_RE_INLINE_WORDS = re.compile(
    r'\b1\s+\w+\s*=\s*([0-9][0-9.,\u00A0 ]*)\s+\w+\b', re.I
)

_BAD_HTML_MARKERS = (
    "enable javascript",
    "access denied",
    "request unsuccessful",
    "captcha",
    "please verify",
    "<title>just a moment...</title>",
)


class XERateError(Exception):
    pass


async def fetch_xe_rate(from_code: str = "EUR", to_code: str = "USD", *, retries: int = 3) -> Decimal:
    """
    Возвращает Decimal-курс из XE для Amount=1 (1 FROM = rate TO).
    Работает и на сервере: HTTP/2, таймауты, fallback-парсинг, ретраи.
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
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Referer": "https://www.xe.com/",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    # отдельные лимиты на connect/read, чтобы лучше понимать зависания
    timeout = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)

    last_err: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers=headers,
                http2=True,              # важно для XE на CloudFront
                follow_redirects=True,
                trust_env=True,          # использует системный CA / прокси
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text or ""

            # проверяем на "пустую" защитную страницу
            low = html.lower()
            if len(html) < 1000 or any(marker in low for marker in _BAD_HTML_MARKERS):
                raise XERateError("Похоже, XE вернул пустую или защитную страницу.")

            # --- ищем курс ---
            num = None
            for pattern in (_RE_OG_DESC, _RE_INLINE_EQ, _RE_INLINE_WORDS):
                m = pattern.search(html)
                if m:
                    num = m.group(1)
                    break

            if not num:
                raise XERateError("Не найдено числовое значение курса в HTML XE.")

            cleaned = (
                num.replace("\u00A0", "")
                   .replace(" ", "")
            )
            if "," in cleaned and "." in cleaned:
                cleaned = cleaned.replace(",", "")
            elif "," in cleaned:
                cleaned = cleaned.replace(",", ".")

            value = Decimal(cleaned)
            if not value.is_finite() or value <= 0 or value > 100_000:
                raise XERateError(f"Некорректное значение курса XE: {value}")

            return value

        except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            last_err = e
        except XERateError as e:
            last_err = e
        except Exception as e:
            last_err = e

        await asyncio.sleep(1.2 * attempt + random.uniform(0.1, 0.3))  # джиттер перед повтором

    err_text = f"{type(last_err).__name__}: {last_err}" if last_err else "неизвестная ошибка"
    raise XERateError(f"Сбой получения курса XE после {retries} попыток: {err_text}")