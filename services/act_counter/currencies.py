from __future__ import annotations

DEFAULT_ACT_CURRENCY = "USDT"
ACT_CURRENCY_PRECISIONS = {
    "USDT": 2,
    "USD": 2,
    "USDW": 2,
    "EUR": 2,
}
ACT_CURRENCY_ALIASES = {
    "usdt": "USDT",
    "юсдт": "USDT",
    "usd": "USD",
    "дол": "USD",
    "долл": "USD",
    "доллар": "USD",
    "доллары": "USD",
    "usdw": "USDW",
    "долб": "USDW",
    "доллбел": "USDW",
    "долбел": "USDW",
    "eur": "EUR",
    "евро": "EUR",
}


def normalize_act_currency_code(raw_code: str | None) -> str | None:
    if raw_code is None:
        return DEFAULT_ACT_CURRENCY
    key = str(raw_code).strip().lower()
    code = ACT_CURRENCY_ALIASES.get(key, key.upper())
    return code if code in ACT_CURRENCY_PRECISIONS else None
