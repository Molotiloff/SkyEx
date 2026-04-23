from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from aiogram.types import CallbackQuery

from db_asyncpg.repo import Repo
from keyboards.request import CB_ISSUE_DONE
from services.cash_requests.legacy_request_parsing import parse_kind_amount_code
from utils.auth import require_manager_or_admin_callback
from utils.formatting import format_amount_core
from utils.info import get_chat_name
from utils.request_text_parser import detect_kind_from_card, parse_amount_code_line

_RE_LINE_AMOUNT = re.compile(r"^\s*Сумма:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.I | re.M)


class RequestIssueService:
    def __init__(
        self,
        *,
        repo: Repo,
        admin_chat_ids: set[int],
        admin_user_ids: set[int],
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids)
        self.admin_user_ids = set(admin_user_ids)

    async def handle(self, cq: CallbackQuery) -> None:
        msg = cq.message
        if not msg:
            await cq.answer()
            return

        if not await require_manager_or_admin_callback(
            self.repo,
            cq,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        text = msg.text or ""

        op_kind: Optional[str] = None
        try:
            parts = (cq.data or "").split(":")
            if len(parts) >= 3 and ":".join(parts[:2]) == CB_ISSUE_DONE:
                op_kind = parts[2].lower()
        except Exception:
            op_kind = None

        if not op_kind:
            op_kind = detect_kind_from_card(text)

        if op_kind not in {"dep", "wd"}:
            await cq.answer("Кнопка доступна только для заявок на внесение или выдачу.", show_alert=True)
            return

        parsed_amt = self._parse_amount_code(text, op_kind=op_kind)
        if not parsed_amt:
            await cq.answer("Не удалось распознать сумму/валюту.", show_alert=True)
            return

        amount_raw, code = parsed_amt
        code = code.upper()

        chat_id = msg.chat.id
        chat_name = get_chat_name(msg)
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)
        acc = next((r for r in accounts if str(r["currency_code"]).upper() == code), None)
        if not acc:
            await cq.message.answer(f"Счёт {code} не найден. Добавьте валюту: /добавь {code} [точность]")
            await cq.answer()
            return

        prec = int(acc.get("precision") or 2)
        q = Decimal(10) ** -prec
        amount = amount_raw.quantize(q).quantize(Decimal("1"))

        idem = f"cash:{chat_id}:{msg.message_id}"
        try:
            if op_kind == "dep":
                await self.repo.deposit(
                    client_id=client_id,
                    currency_code=code,
                    amount=amount,
                    comment="cash issue",
                    source="cash_request",
                    idempotency_key=idem,
                )
            else:
                await self.repo.withdraw(
                    client_id=client_id,
                    currency_code=code,
                    amount=amount,
                    comment="cash issue",
                    source="cash_request",
                    idempotency_key=idem,
                )
        except Exception as e:
            await cq.message.answer(f"Не удалось провести операцию по кошельку: {e}")
            await cq.answer()
            return

        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        accounts2 = await self.repo.snapshot_wallet(client_id)
        acc2 = next((r for r in accounts2 if str(r["currency_code"]).upper() == code), None)
        cur_bal = Decimal(str(acc2["balance"])) if acc2 else Decimal("0")
        prec2 = int(acc2.get("precision") or prec) if acc2 else prec
        pretty_bal = format_amount_core(cur_bal, prec2)

        await cq.message.answer(
            f"Запомнил.\nБаланс: <code>{pretty_bal} {code.lower()}</code>",
            parse_mode="HTML",
        )
        await cq.answer("Отмечено как выдано")

    @staticmethod
    def _parse_amount_code(text: str, *, op_kind: str) -> tuple[Decimal, str] | None:
        m_amt = _RE_LINE_AMOUNT.search(text or "")
        if m_amt:
            return parse_amount_code_line(m_amt.group(1))

        legacy = parse_kind_amount_code(text or "")
        if not legacy:
            return None

        kind_ru, amount, code = legacy
        if op_kind == "dep" and kind_ru != "Депозит":
            return None
        if op_kind == "wd" and kind_ru != "Выдача":
            return None
        return amount, code
