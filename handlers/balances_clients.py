# handlers/balances_clients.py
import html
import re
from collections import defaultdict
from decimal import Decimal
from typing import Iterable

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo
from utils.formatting import format_amount_core

MINUS_CHARS = "-−–—"
PLUS_CHARS  = "+＋"

# Порог для «почти нулевых» остатков (|bal| < THRESHOLD -> пропускаем)
NEAR_ZERO_THRESHOLD = Decimal("1")

# Алиасы валют (принимаем латиницу и кириллицу, приводим к коду)
ALIASES = {
    # RUB
    "RUB": "RUB", "РУБ": "RUB", "РУБЛЬ": "RUB", "РУБЛИ": "RUB", "РУБЛЕЙ": "RUB", "РУБ.": "RUB",
    # USD
    "USD": "USD", "ДОЛ": "USD", "ДОЛЛ": "USD", "ДОЛЛАР": "USD", "ДОЛЛАРЫ": "USD",
    # USDT
    "USDT": "USDT", "ЮСДТ": "USDT",
    # EUR
    "EUR": "EUR", "ЕВРО": "EUR",
    # USDW (условный «доллар белый»)
    "USDW": "USDW", "ДОЛБ": "USDW", "ДОЛЛБЕЛ": "USDW", "ДОЛБЕЛ": "USDW",
}


def _normalize_code(code: str) -> str:
    code_up = (code or "").strip().upper()
    return ALIASES.get(code_up, code_up)


def _normalize_sign(ch: str) -> str:
    ch = (ch or "").strip()
    if ch in MINUS_CHARS:
        return "-"
    if ch in PLUS_CHARS:
        return "+"
    return ch


def _chunk(text: str, limit: int = 3500) -> list[str]:
    out, cur, total = [], [], 0
    for line in text.splitlines(True):
        if total + len(line) > limit and cur:
            out.append("".join(cur))
            cur, total = [], 0
        cur.append(line)
        total += len(line)
    if cur:
        out.append("".join(cur))
    return out


class ClientsBalancesHandler:
    """
    /бк <ВАЛЮТА> <+|-> — клиенты с положительным/отрицательным балансом по валюте.
    /бк <ВАЛЮТА>       — все клиенты с балансом по валюте, НО с фильтром |баланс| >= 1.
    /бк                — все ненулевые балансы по всем валютам, сгруппировано по клиентам.
    """

    def __init__(self, repo: Repo, admin_chat_ids: Iterable[int] | None = None) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.router = Router()
        self._register()

    async def _cmd_balances(self, message: Message) -> None:
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Команда доступна только в админском чате.")
            return

        text = (message.text or "")

        # 1) Вид: /бк <валюта> <знак>
        m_with_sign = re.match(
            rf"(?iu)^/бк(?:@\w+)?\s+(\S+)\s+([{re.escape(MINUS_CHARS + PLUS_CHARS)}])\s*$",
            text
        )

        # 2) Вид: /бк <валюта>   (без знака)
        m_only_code = re.match(
            r"(?iu)^/бк(?:@\w+)?\s+(\S+)\s*$",
            text
        )

        rows = await self.repo.balances_by_client()

        # --- вариант 1: фильтр по валюте и знаку ---
        if m_with_sign:
            code_filter = _normalize_code(m_with_sign.group(1))
            sign_filter = _normalize_sign(m_with_sign.group(2))

            filtered = []
            for r in rows:
                if str(r["currency_code"]).upper() != code_filter:
                    continue
                bal = Decimal(str(r["balance"]))
                if sign_filter == "+" and bal <= 0:
                    continue
                if sign_filter == "-" and bal >= 0:
                    continue
                filtered.append(r)

            if not filtered:
                await message.answer(
                    f"Нет клиентов с балансом по {html.escape(code_filter)} со знаком '{sign_filter}'.",
                    parse_mode="HTML")
                return

            filtered.sort(key=lambda r: (r.get("client_name") or "").lower())

            out_lines: list[str] = []
            cmp_html = "&gt; 0" if sign_filter == "+" else "&lt; 0"
            head = f"Клиенты по {html.escape(code_filter)} ({cmp_html}):"
            out_lines = [head, ""]

            for r in filtered:
                name = html.escape(r.get("client_name") or "")
                bal = Decimal(str(r["balance"]))
                prec = int(r.get("precision", 2))
                pretty = html.escape(format_amount_core(bal, prec))
                out_lines.append(f"{name}")
                out_lines.append("-------")
                out_lines.append(f"  {pretty} {code_filter.lower()}")
                out_lines.append("")

            text_out = "\n".join(out_lines)
            for chunk in _chunk(text_out):
                await message.answer(chunk, parse_mode="HTML")
            return

        # --- вариант 2: только валюта, без знака (|bal| >= 1) ---
        if m_only_code:
            code_filter = _normalize_code(m_only_code.group(1))

            filtered = []
            for r in rows:
                if str(r["currency_code"]).upper() != code_filter:
                    continue
                bal = Decimal(str(r["balance"]))
                if bal.copy_abs() < NEAR_ZERO_THRESHOLD:
                    continue  # пропускаем «почти нули»
                filtered.append(r)

            if not filtered:
                await message.answer(
                    f"Нет клиентов с балансом по {html.escape(code_filter)} (|баланс| ≥ {NEAR_ZERO_THRESHOLD}).",
                    parse_mode="HTML")
                return

            filtered.sort(key=lambda r: (r.get("client_name") or "").lower())

            out_lines: list[str] = [
                f"Клиенты по {html.escape(code_filter)} (|баланс| ≥ {NEAR_ZERO_THRESHOLD}):",
                ""
            ]
            for r in filtered:
                name = html.escape(r.get("client_name") or "")
                bal = Decimal(str(r["balance"]))
                prec = int(r.get("precision", 2))
                pretty = html.escape(format_amount_core(bal, prec))
                out_lines.append(f"{name}")
                out_lines.append("-------")
                out_lines.append(f"  {pretty} {code_filter.lower()}")
                out_lines.append("")

            text_out = "\n".join(out_lines)
            for chunk in _chunk(text_out):
                await message.answer(chunk, parse_mode="HTML")
            return

        # --- вариант 3: все ненулевые балансы по всем валютам ---
        by_client: dict[int, dict] = defaultdict(lambda: {"name": "", "chat_id": None, "items": []})
        for r in rows:
            cid = int(r["client_id"])
            bal = Decimal(str(r["balance"]))
            if bal == Decimal("0"):
                continue
            code = _normalize_code(str(r["currency_code"]))
            prec = int(r.get("precision", 2))
            by_client[cid]["name"] = r.get("client_name") or ""
            by_client[cid]["chat_id"] = r.get("chat_id")
            by_client[cid]["items"].append((code, prec, bal))

        if not by_client:
            await message.answer("У всех клиентов нулевые балансы.")
            return

        out_lines: list[str] = ["Все ненулевые балансы:", ""]
        for cid, data in sorted(by_client.items(), key=lambda x: (x[1]["name"] or "").lower()):
            name = html.escape(data["name"])
            out_lines.append(f"{name}")
            out_lines.append("-------")
            for code, prec, bal in sorted(data["items"]):
                pretty = html.escape(format_amount_core(bal, prec))
                out_lines.append(f"  {pretty} {code.lower()}")
            out_lines.append("")

        text_out = "\n".join(out_lines)
        for chunk in _chunk(text_out):
            await message.answer(chunk, parse_mode="HTML")

    def _register(self) -> None:
        self.router.message.register(self._cmd_balances, Command("бк"))
        # Разрешаем /бк, /бк CODE, /бк CODE SIGN
        self.router.message.register(
            self._cmd_balances,
            F.text.regexp(
                rf"(?iu)^/бк(?:@\w+)?(?:\s+\S+(?:\s+[+\-−–—])?\s*)?$"
            ),
        )
