from __future__ import annotations

import asyncio
import re
from decimal import Decimal

import httpx

_XE_URL_TMPL = "https://www.xe.com/currencyconverter/convert/?Amount=1&From={FROM}&To={TO}"

# 1) og:description
_RE_OG_DESC = re.compile(
    r'property=["\']og:description["\']\s+content=["\'][^"\']*?=\s*([0-9][0-9.,\u00A0 ]*)\s', re.I
)
# 2) «1 EUR = 1.0957 USD»
_RE_INLINE_EQ = re.compile(
    r'\b1\s+[A-Z]{3}\s*=\s*([0-9][0-9.,\u00A0 ]*)\s+[A-Z]{3}\b', re.I
)
# 3) на некоторых локалях бывает «1 Euro = 1,0957 US Dollar»
_RE_INLINE_WORDS = re.compile(
    r'\b1\s+\w+\s*=\s*([0-9][0-9.,\u00A0 ]*)\s+\w+\b', re.I
)

# маркеры «не тот HTML» (интерстициальная страница, антибот, т.п.)
_BAD_HTML_MARKERS = (
    "enable javascript",
    "access denied",
    "request unsuccessful",
    "captcha",
    "please verify",
)

class XERateError(Exception):
    pass


async def fetch_xe_rate(from_code: str = "EUR", to_code: str = "USD", *, retries: int = 3) -> Decimal:
    """
    Возвращает Decimal-курс из XE для Amount=1 (1 FROM = rate TO).
    Делает несколько попыток при таймаутах/плохом HTML. Бросает XERateError.
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
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://www.xe.com/",
        "Upgrade-Insecure-Requests": "1",
    }

    # раздельные таймауты помогают понять, где застряли
    timeout = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)

    last_err: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers=headers,
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text or ""

            low = html.lower()
            if any(marker in low for marker in _BAD_HTML_MARKERS):
                raise XERateError("Страница XE требует JS/верификацию (interstitial).")

            # пробуем разные варианты парсинга
            num = None
            m = _RE_OG_DESC.search(html)
            if m:
                num = m.group(1)
            else:
                m2 = _RE_INLINE_EQ.search(html)
                if m2:
                    num = m2.group(1)
                else:
                    m3 = _RE_INLINE_WORDS.search(html)
                    if m3:
                        num = m3.group(1)

            if not num:
                # если в этот момент HTML «настоящий», но без нужных кусков — трактуем как временный сбой и ретраим
                raise XERateError("Не найдено числовое значение курса в HTML XE.")

            # нормализация: убираем пробелы/неразрывные и запятые как разделители тысяч
            # оставляем точку или запятую как десятичную — преобразуем к точке
            cleaned = (
                num.replace("\u00A0", "")
                   .replace(" ", "")
            )
            # если в числе есть и точка и запятая, попробуем heuristic:
            #   - если запятая справа от точки — вероятно запятая = тысячи → убираем запятую
            #   - если только запятая — меняем её на точку
            if "," in cleaned and "." in cleaned:
                # убираем запятые как разделители тысяч
                cleaned = cleaned.replace(",", "")
            elif "," in cleaned:
                cleaned = cleaned.replace(",", ".")

            return Decimal(cleaned)

        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ConnectError) as e:
            last_err = e
        except XERateError as e:
            # сохранить последнюю — и попробовать ретрай, вдруг интерстициальная раздача была моментная
            last_err = e
        except Exception as e:
            # непредвиденная ошибка — тоже попробуем ещё раз
            last_err = e

        # бэкофф перед следующей попыткой
        await asyncio.sleep(0.8 * attempt)

    # если дошли сюда — все попытки не удались
    err_text = f"{type(last_err).__name__}: {last_err}" if last_err else "неизвестная ошибка"
    raise XERateError(f"Сбой получения курса XE после {retries} попыток: {err_text}")