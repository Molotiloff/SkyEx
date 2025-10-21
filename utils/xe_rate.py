from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from typing import Optional

import httpx

_XE_URL_TMPL = "https://www.xe.com/currencyconverter/convert/?Amount=1&From={FROM}&To={TO}"

# Варианты og:description у XE встречаются разные:
#   "XE Currency Converter: 1 Euro = 1.0957 US Dollar"
#   "XE Currency Converter: 1 USD to EUR = 0.92 EUR"
#   "XE Currency Converter: 1 US Dollar equals 0.92 Euro"
_RE_OG_DESC = re.compile(
    r'<meta\s+(?:property|name)=["\']og:description["\']\s+content=["\'][^"\']*?=\s*([0-9][0-9.,\u00A0 ]*)\s',
    re.I,
)

# запасные варианты в тексте:
_RE_INLINE_EQ = re.compile(r'\b1\s+[A-Z]{3}\s*=\s*([0-9][0-9.,\u00A0 ]*)\s+[A-Z]{3}\b', re.I)
_RE_INLINE_WORDS = re.compile(r'\b1\s+\w+\s*=\s*([0-9][0-9.,\u00A0 ]*)\s+\w+\b', re.I)

_BAD_HTML_MARKERS = (
    "enable javascript",
    "access denied",
    "request unsuccessful",
    "captcha",
    "please verify",
    "just a moment",
)

class XERateError(Exception):
    pass


def _normalize_number(s: str) -> Decimal:
    """ '1 234,56' | '1 234.56' | '1,234.56' -> Decimal('1234.56') """
    cleaned = s.replace("\u00A0", "").replace(" ", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")  # запятая была тысячным разделителем
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    val = Decimal(cleaned)
    if not val.is_finite() or val <= 0 or val > 100_000:
        raise XERateError(f"Некорректное значение курса XE: {val}")
    return val


async def fetch_xe_rate(from_code: str = "EUR", to_code: str = "USD", *, retries: int = 3) -> Decimal:
    from_code = (from_code or "").strip().upper()
    to_code = (to_code or "").strip().upper()
    if not from_code or not to_code:
        raise XERateError("Не заданы коды валют.")

    url = _XE_URL_TMPL.format(FROM=from_code, TO=to_code)

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.0.0 Safari/537.36"),
        "Accept": ("text/html,application/xhtml+xml,application/xml;"
                   "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"),
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

    timeout = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)
    last_err: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers=headers,
                http2=True,          # важно для XE/CloudFront
                follow_redirects=True,
                trust_env=True,      # использовать системные CA / прокси, если есть
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text or ""

            low = html.lower()
            if len(html) < 2000 or any(marker in low for marker in _BAD_HTML_MARKERS):
                raise XERateError("Похоже, XE вернул пустую/защитную страницу.")

            # Порядок: og:description → «1 EUR = X USD» → словесный
            for rx in (_RE_OG_DESC, _RE_INLINE_EQ, _RE_INLINE_WORDS):
                m = rx.search(html)
                if m:
                    return _normalize_number(m.group(1))

            raise XERateError("Не найдено числовое значение курса в HTML XE.")

        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ConnectError) as e:
            last_err = e
        except XERateError as e:
            last_err = e
        except Exception as e:
            last_err = e

        # небольшой бэкофф с джиттером
        await asyncio.sleep(1.0 * attempt + 0.2 * attempt)

    raise XERateError(f"Сбой получения курса XE после {retries} попыток: {type(last_err).__name__}: {last_err}")


# --- Диагностический хелпер: запусти один раз на сервере ---
async def diagnose_xe(from_code="USD", to_code="EUR"):
    url = _XE_URL_TMPL.format(FROM=from_code, TO=to_code)
    async with httpx.AsyncClient(http2=True, follow_redirects=True, timeout=httpx.Timeout(10.0, read=20.0)) as c:
        r = await c.get(url)
        print("status:", r.status_code)
        print("len(html):", len(r.text))
        print("og:desc match:", bool(_RE_OG_DESC.search(r.text)))
        print("inline eq match:", bool(_RE_INLINE_EQ.search(r.text)))
        print("inline words match:", bool(_RE_INLINE_WORDS.search(r.text)))
        # покажем первые 200 символов вокруг совпадения, если есть
        for name, rx in (("og", _RE_OG_DESC), ("eq", _RE_INLINE_EQ), ("words", _RE_INLINE_WORDS)):
            m = rx.search(r.text)
            if m:
                s, e = max(0, m.start()-100), min(len(r.text), m.end()+100)
                print(f"[{name}] sample:", r.text[s:e].replace("\n", " ")[:400])

