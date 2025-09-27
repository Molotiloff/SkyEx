# handlers/accept_short.py
from __future__ import annotations

import html
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo
from utils.exchange_base import AbstractExchangeHandler
from utils.calc import evaluate, CalcError
from utils.auth import require_manager_or_admin_message
from utils.formatting import format_amount_core
from utils.info import get_chat_name
from utils.requests import post_request_message
from utils.req_index import req_index
from utils.format_wallet_compact import format_wallet_compact


def _fmt_rate(d: Decimal) -> str:
    s = f"{d.normalize():f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


# Ищем номер заявки в тексте сообщения бота
_RE_REQ_ID = re.compile(r"Заявка:\s*(?:<code>)?(\d{6,})(?:</code>)?", re.IGNORECASE)


class AcceptShortHandler(AbstractExchangeHandler):
    """
    /пд|/пе|/пт|/пр|/пб <recv_amount_expr> <од|ое|от|ор|об> <pay_amount_expr> [комментарий]

    Принимаем слева — списываем у клиента; отдаём справа — зачисляем клиенту.
    Если команда отправлена в ответ на сообщение с заявкой (бота ИЛИ исходную команду) —
    редактируем существующую заявку (только менеджеры/админы).
    """
    RECV_MAP = {"пд": "USD", "пе": "EUR", "пт": "USDT", "пр": "RUB", "пб": "USDW"}
    PAY_MAP = {"од": "USD", "ое": "EUR", "от": "USDT", "ор": "RUB", "об": "USDW"}

    def __init__(
        self,
        repo: Repo,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
        request_chat_id: int | None = None,
    ) -> None:
        super().__init__(repo, request_chat_id=request_chat_id)
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.router = Router()
        self._register()

    async def _cmd_accept_short(self, message: Message) -> None:
        # доступ: админ-чат / админ-пользователь / менеджер
        if not await require_manager_or_admin_message(
            self.repo, message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        raw = (message.text or "")
        m = re.match(
            r"^/(пд|пе|пт|пр|пб)(?:@\w+)?\s+(.+?)\s+(од|ое|от|ор|об)\s+(\S+)(?:\s+(.+))?$",
            raw, flags=re.IGNORECASE | re.UNICODE
        )
        if not m:
            await message.answer(
                "Формат:\n"
                "  /пд|/пе|/пт|/пр|/пб <сумма/expr> <од|ое|от|ор|об> <сумма/expr> [комментарий]\n\n"
                "Примеры:\n"
                "• /пд 1000 ое 1000/0.92 Клиент Петров\n"
                "• /пе (2500+500) ор 300000 «наличные»\n"
                "• /пт 700 од 700*1.08 срочно\n"
                "• /пр 100000 от 100000/94 договор №42"
            )
            return

        recv_key = m.group(1).lower()
        recv_amount_expr = m.group(2).strip()
        pay_key = m.group(3).lower()
        pay_amount_expr = m.group(4).strip()
        user_note = (m.group(5) or "").strip()

        recv_code = self.RECV_MAP.get(recv_key)
        pay_code = self.PAY_MAP.get(pay_key)
        if not recv_code or not pay_code:
            await message.answer("Не распознал валюты. Используйте: /пд /пе /пт /пр /пб и од/ое/от/ор/об.")
            return

        # Валидируем выражения (без комментария)
        try:
            recv_raw = evaluate(recv_amount_expr)
            pay_raw = evaluate(pay_amount_expr)
            if recv_raw <= 0 or pay_raw <= 0:
                await message.answer("Суммы должны быть > 0")
                return
            _ = recv_raw / pay_raw  # проверка деления
        except (CalcError, InvalidOperation, ZeroDivisionError) as e:
            await message.answer(f"Ошибка в выражениях: {e}")
            return

        # Проверка: редактирование?
        reply_msg = getattr(message, "reply_to_message", None)
        is_edit = False
        edit_req_id: str | None = None
        target_bot_msg_id: int | None = None

        if reply_msg and (reply_msg.text or ""):
            if reply_msg.from_user and reply_msg.from_user.id == message.bot.id:
                # Ответ на сообщение БОТА — достаём req_id
                mid = _RE_REQ_ID.search(reply_msg.text)
                if mid:
                    is_edit = True
                    edit_req_id = mid.group(1)
                    target_bot_msg_id = reply_msg.message_id
            else:
                # Ответ на исходную команду пользователя — ищем связку в индексе
                link = req_index.lookup(message.chat.id, reply_msg.message_id)
                if link is not None:
                    is_edit = True
                    edit_req_id = link.req_id
                    target_bot_msg_id = link.bot_msg_id

        # Точности счётов (для форматирования), без изменения балансов
        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)

        def _find_acc(code: str):
            return next((r for r in accounts if str(r["currency_code"]).upper() == code), None)

        acc_recv = _find_acc(recv_code)
        acc_pay = _find_acc(pay_code)
        if not acc_recv or not acc_pay:
            missing = recv_code if not acc_recv else pay_code
            await message.answer(f"Счёт {missing} не найден. Добавьте валюту: /добавь {missing} [точность]")
            return

        recv_prec = int(acc_recv["precision"])
        pay_prec = int(acc_pay["precision"])

        # Квантуем и считаем курс «как людям удобно»
        q_recv = Decimal(10) ** -recv_prec
        q_pay = Decimal(10) ** -pay_prec
        recv_amount = recv_raw.quantize(q_recv, rounding=ROUND_HALF_UP)
        pay_amount = pay_raw.quantize(q_pay,  rounding=ROUND_HALF_UP)
        if recv_amount == 0 or pay_amount == 0:
            await message.answer("Сумма слишком мала для точности выбранных валют.")
            return

        try:
            if recv_code == "RUB" or pay_code == "RUB":
                rub_raw = recv_raw if recv_code == "RUB" else pay_raw
                other_raw = pay_raw if recv_code == "RUB" else recv_raw
                rate = rub_raw / other_raw
            else:
                rate = pay_raw / recv_raw
            if not rate.is_finite() or rate <= 0:
                await message.answer("Курс невалидный.")
                return
            rate_str = _fmt_rate(rate.quantize(Decimal("1e-8")))
        except (InvalidOperation, ZeroDivisionError):
            await message.answer("Ошибка расчёта курса.")
            return

        pretty_recv = format_amount_core(recv_amount, recv_prec)
        pretty_pay = format_amount_core(pay_amount,  pay_prec)

        # === РЕЖИМ РЕДАКТИРОВАНИЯ ===
        if is_edit and edit_req_id and target_bot_msg_id:
            # Собираем новый текст для клиента (без «Клиент» и без «Формула»)
            parts_client = [
                f"Заявка: <code>{edit_req_id}</code>",
                "-----",
                f"Получаем: <code>{pretty_recv} {recv_code.lower()}</code>",
                f"Курс: <code>{rate_str}</code>",
                f"Отдаём: <code>{pretty_pay} {pay_code.lower()}</code>",
            ]
            if user_note:
                parts_client += ["----", f"Комментарий: <code>{html.escape(user_note)}</code>"]
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            parts_client += ["----", f"Изменение: <code>{ts}</code>"]
            new_client_text = "\n".join(parts_client)

            # Пересчёт балансов (если есть текст старой заявки бота)
            did_recalc = False
            try:
                if reply_msg and reply_msg.from_user and reply_msg.from_user.id == message.bot.id:
                    did_recalc = await self.apply_edit_delta(
                        client_id=client_id,
                        old_request_text=reply_msg.text or "",
                        recv_code_new=recv_code,
                        pay_code_new=pay_code,
                        recv_amount_new=recv_amount,
                        pay_amount_new=pay_amount,
                        recv_prec=recv_prec,
                        pay_prec=pay_prec,
                        chat_id=chat_id,
                        target_bot_msg_id=target_bot_msg_id,
                        cmd_msg_id=message.message_id,
                        recv_is_deposit=False,  # здесь «принимаем» = списываем
                        pay_is_withdraw=False,  # «отдаём» = зачисляем
                    )
            except Exception as e:
                await message.answer(f"Не удалось пересчитать балансы: {e}")

            # Обновляем текст заявки
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=target_bot_msg_id,
                    text=new_client_text,
                    parse_mode="HTML",
                )
            except Exception as e:
                await message.answer(f"Не удалось изменить заявку: {e}")
                return

            # В заявочный чат — предупреждение
            if self.request_chat_id:
                alert_text = f"⚠️ Внимание: заявка <code>{edit_req_id}</code> изменена."
                try:
                    await post_request_message(
                        bot=message.bot, request_chat_id=self.request_chat_id,
                        text=alert_text, reply_markup=None,
                    )
                except Exception:
                    pass

            # Показываем актуальные балансы (как /дай)
            rows = await self.repo.snapshot_wallet(client_id)
            compact = format_wallet_compact(rows, only_nonzero=True)
            if compact == "Пусто":
                await message.answer("Все счета нулевые. Посмотреть всё: /кошелек")
            else:
                safe_title = html.escape(f"Средств у {chat_name}:")
                safe_rows = html.escape(compact)
                await message.answer(f"<code>{safe_title}\n\n{safe_rows}</code>", parse_mode="HTML")

            if not did_recalc:
                await message.answer("ℹ️ Чтобы автоматически пересчитать баланс, отвечайте "
                                     "на сообщение БОТА с заявкой.")
            return

        # === СОЗДАНИЕ НОВОЙ ЗАЯВКИ ===
        await self.process(
            message,
            recv_code=recv_code,
            recv_amount_expr=recv_amount_expr,
            pay_code=pay_code,
            pay_amount_expr=pay_amount_expr,
            recv_is_deposit=False,   # принимаем — списываем у клиента
            pay_is_withdraw=False,   # отдаём — зачисляем клиенту
            note=user_note or None,
        )

    def _register(self) -> None:
        self.router.message.register(self._cmd_accept_short, Command("пд"))
        self.router.message.register(self._cmd_accept_short, Command("пе"))
        self.router.message.register(self._cmd_accept_short, Command("пт"))
        self.router.message.register(self._cmd_accept_short, Command("пр"))
        self.router.message.register(self._cmd_accept_short, Command("пб"))
        self.router.message.register(
            self._cmd_accept_short,
            F.text.regexp(r"(?iu)^/(пд|пе|пт|пр|пб)(?:@\w+)?\b"),
        )
