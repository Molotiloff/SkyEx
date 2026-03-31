from __future__ import annotations

import html
import re
from collections import defaultdict
from decimal import Decimal
from typing import Iterable

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo
from utils.formatting import format_amount_core

MINUS_CHARS = "-−–—"
PLUS_CHARS = "+＋"

NEAR_ZERO_THRESHOLD = Decimal("1")
SCHEDULED_RUB_DEBT_THRESHOLD = Decimal("-1000")
SCHEDULED_USDT_DEBT_THRESHOLD = Decimal("-1")
SCHEDULED_RUB_POSITIVE_THRESHOLD = Decimal("1000")
SCHEDULED_USDT_POSITIVE_THRESHOLD = Decimal("100")
EXCLUDED_SCHEDULED_GROUP = "Балансы"

ALIASES = {
    "RUB": "RUB", "РУБ": "RUB", "РУБЛЬ": "RUB", "РУБЛИ": "RUB", "РУБЛЕЙ": "RUB", "РУБ.": "RUB",
    "USD": "USD", "ДОЛ": "USD", "ДОЛЛ": "USD", "ДОЛЛАР": "USD", "ДОЛЛАРЫ": "USD",
    "USDT": "USDT", "ЮСДТ": "USDT",
    "EUR": "EUR", "ЕВРО": "EUR",
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
    /бк <ВАЛЮТА> — все клиенты с балансом по валюте, НО с фильтром |баланс| >= 1.
    /бк — все ненулевые балансы по всем валютам, сгруппировано по клиентам.
    """

    def __init__(self, repo: Repo, admin_chat_ids: Iterable[int] | None = None) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.router = Router()
        self._register()

    async def _build_report(
        self,
        *,
        code_filter: str | None = None,
        sign_filter: str | None = None,
        min_negative_balance: Decimal | None = None,
        min_positive_balance: Decimal | None = None,
        excluded_client_group: str | None = None,
    ) -> list[str]:
        rows = await self.repo.balances_by_client()

        if code_filter and sign_filter:
            code_filter = _normalize_code(code_filter)
            sign_filter = _normalize_sign(sign_filter)
            excluded_group_norm = (excluded_client_group or "").strip().casefold()

            filtered = []
            for r in rows:
                if str(r["currency_code"]).upper() != code_filter:
                    continue

                client_group = str(r.get("client_group") or "").strip()
                if excluded_group_norm and client_group.casefold() == excluded_group_norm:
                    continue

                bal = Decimal(str(r["balance"]))

                if sign_filter == "+":
                    if bal <= 0:
                        continue
                    if min_positive_balance is not None and bal <= min_positive_balance:
                        continue

                if sign_filter == "-":
                    if bal >= 0:
                        continue
                    if min_negative_balance is not None and bal >= min_negative_balance:
                        continue

                filtered.append(r)

            if not filtered:
                if sign_filter == "-" and min_negative_balance is not None:
                    return [
                        f"Нет клиентов по {html.escape(code_filter)} "
                        f"с балансом меньше {html.escape(str(min_negative_balance))}."
                    ]
                if sign_filter == "+" and min_positive_balance is not None:
                    return [
                        f"Нет клиентов по {html.escape(code_filter)} "
                        f"с балансом больше {html.escape(str(min_positive_balance))}."
                    ]

                cmp_html = "&gt; 0" if sign_filter == "+" else "&lt; 0"
                return [f"Нет клиентов по {html.escape(code_filter)} ({cmp_html})."]

            if sign_filter == "-":
                filtered.sort(
                    key=lambda r: (
                        Decimal(str(r["balance"])),
                        (r.get("client_name") or "").lower(),
                    )
                )
            else:
                filtered.sort(
                    key=lambda r: (
                        -Decimal(str(r["balance"])),
                        (r.get("client_name") or "").lower(),
                    )
                )

            if sign_filter == "-" and min_negative_balance is not None:
                header = (
                    f"Клиенты по {html.escape(code_filter)} "
                    f"(баланс меньше {html.escape(str(min_negative_balance))}):"
                )
            elif sign_filter == "+" and min_positive_balance is not None:
                header = (
                    f"Клиенты по {html.escape(code_filter)} "
                    f"(баланс больше {html.escape(str(min_positive_balance))}):"
                )
            else:
                cmp_html = "&gt; 0" if sign_filter == "+" else "&lt; 0"
                header = f"Клиенты по {html.escape(code_filter)} ({cmp_html}):"

            out_lines: list[str] = [header, ""]

            for r in filtered:
                name = html.escape(r.get("client_name") or "")
                bal = Decimal(str(r["balance"]))
                prec = int(r.get("precision", 2))
                pretty = html.escape(format_amount_core(bal, prec))
                out_lines.append(name)
                out_lines.append(f"  {pretty} {code_filter.lower()}")
                out_lines.append("—————————")

            return _chunk("\n".join(out_lines))

        if code_filter:
            code_filter = _normalize_code(code_filter)

            filtered = []
            for r in rows:
                if str(r["currency_code"]).upper() != code_filter:
                    continue
                bal = Decimal(str(r["balance"]))
                if bal.copy_abs() < NEAR_ZERO_THRESHOLD:
                    continue
                filtered.append(r)

            if not filtered:
                return [
                    f"Нет клиентов с балансом по {html.escape(code_filter)} "
                    f"(|баланс| ≥ {NEAR_ZERO_THRESHOLD})."
                ]

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
                out_lines.append(name)
                out_lines.append(f"  {pretty} {code_filter.lower()}")
                out_lines.append("—————————")

            return _chunk("\n".join(out_lines))

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
            return ["У всех клиентов нулевые балансы."]

        out_lines: list[str] = ["Все ненулевые балансы:", ""]
        for _, data in sorted(by_client.items(), key=lambda x: (x[1]["name"] or "").lower()):
            name = html.escape(data["name"])
            out_lines.append(name)
            for code, prec, bal in sorted(data["items"]):
                pretty = html.escape(format_amount_core(bal, prec))
                out_lines.append(f"  {pretty} {code.lower()}")
            out_lines.append("—————————")

        return _chunk("\n".join(out_lines))

    async def send_scheduled_negative_balances(
        self,
        bot: Bot,
        *,
        currencies: Iterable[str] = ("RUB", "USDT"),
    ) -> None:
        _ = currencies  # оставляем параметр для совместимости

        scheduled_sections = [
            {
                "code": "RUB",
                "sign": "-",
                "min_negative_balance": SCHEDULED_RUB_DEBT_THRESHOLD,
                "min_positive_balance": None,
            },
            {
                "code": "USDT",
                "sign": "-",
                "min_negative_balance": SCHEDULED_USDT_DEBT_THRESHOLD,
                "min_positive_balance": None,
            },
        ]

        for chat_id in self.admin_chat_ids:
            await bot.send_message(
                chat_id=chat_id,
                text="📊 <b>Ежедневный отчёт по балансам</b>",
                parse_mode="HTML",
            )

            has_any = False

            for section in scheduled_sections:
                chunks = await self._build_report(
                    code_filter=section["code"],
                    sign_filter=section["sign"],
                    min_negative_balance=section["min_negative_balance"],
                    min_positive_balance=section["min_positive_balance"],
                    excluded_client_group=EXCLUDED_SCHEDULED_GROUP,
                )

                if not chunks:
                    continue

                if len(chunks) == 1:
                    text_only = chunks[0].strip().lower()
                    if text_only.startswith("нет клиентов"):
                        continue

                has_any = True
                for chunk in chunks:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        parse_mode="HTML",
                    )

            if not has_any:
                await bot.send_message(
                    chat_id=chat_id,
                    text="Подходящих балансов по выбранным условиям нет.",
                    parse_mode="HTML",
                )

    async def _cmd_balances(self, message: Message) -> None:
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Команда доступна только в админском чате.")
            return

        text = (message.text or "")

        m_with_sign = re.match(
            rf"(?iu)^/бк(?:@\w+)?\s+(\S+)\s+([{re.escape(MINUS_CHARS + PLUS_CHARS)}])\s*$",
            text,
        )
        m_only_code = re.match(
            r"(?iu)^/бк(?:@\w+)?\s+(\S+)\s*$",
            text,
        )

        if m_with_sign:
            code_filter = m_with_sign.group(1)
            sign_filter = m_with_sign.group(2)
            chunks = await self._build_report(
                code_filter=code_filter,
                sign_filter=sign_filter,
            )
            for chunk in chunks:
                await message.answer(chunk, parse_mode="HTML")
            return

        if m_only_code:
            code_filter = m_only_code.group(1)
            chunks = await self._build_report(code_filter=code_filter)
            for chunk in chunks:
                await message.answer(chunk, parse_mode="HTML")
            return

        chunks = await self._build_report()
        for chunk in chunks:
            await message.answer(chunk, parse_mode="HTML")

    def _register(self) -> None:
        self.router.message.register(self._cmd_balances, Command("бк"))
        self.router.message.register(
            self._cmd_balances,
            F.text.regexp(
                rf"(?iu)^/бк(?:@\w+)?(?:\s+\S+(?:\s+[+\-−–—])?\s*)?$"
            ),
        )