# utils/exchange_base.py
from __future__ import annotations

import html
import random
import re
from abc import ABC
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from db_asyncpg.repo import Repo
from utils.calc import CalcError, evaluate
from utils.format_wallet_compact import format_wallet_compact
from utils.formatting import format_amount_core
from utils.info import _fmt_rate, get_chat_name
# --- индексы соответствий «команда ↔ сообщение бота» ---
from utils.req_index import req_index
from utils.requests import post_request_message

# --- Вспомогательное: парсинг строк из старой заявки ---
_SEP = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}
_RE_GET = re.compile(r"^Получаем:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.M | re.I)
_RE_GIVE = re.compile(r"^Отдаём:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.M | re.I)

# Ищем номер заявки в тексте сообщения бота
_RE_REQ_ID = re.compile(r"Заявка:\s*(?:<code>)?(\d{6,})(?:</code>)?", re.IGNORECASE)

# Для извлечения «Создал: ...» из старого текста
_RE_CREATED_BY = re.compile(r"^\s*Создал:\s*(?:<b>)?(.+?)(?:</b>)?\s*$", re.I | re.M)


def _parse_amt_code(payload: str) -> tuple[Decimal, str] | None:
    """'100'000.00 rub' -> (Decimal(...), 'RUB')"""
    try:
        amt_str, code = payload.rsplit(" ", 1)
    except ValueError:
        return None
    for ch in _SEP:
        amt_str = amt_str.replace(ch, "")
    amt_str = amt_str.replace(",", ".").strip()
    try:
        return Decimal(amt_str), code.strip().upper()
    except Exception:
        return None


def _cancel_kb(req_id: int | str) -> InlineKeyboardMarkup:
    """Кнопка под клиентской заявкой — «Отменить заявку»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отменить заявку", callback_data=f"req_cancel:{req_id}")]
        ]
    )


class AbstractExchangeHandler(ABC):
    """
    Базовая реализация обмена (под Postgres Repo).
    """

    def __init__(self, repo: Repo, request_chat_id: int | None = None) -> None:
        self.repo = repo
        self.request_chat_id = request_chat_id

    # ================================================================
    # Перерасчёт баланса при редактировании существующей заявки
    # (используется хендлером при ответе на сообщение бота)
    # ================================================================
    async def apply_edit_delta(
        self,
        *,
        client_id: int,
        old_request_text: str,
        recv_code_new: str,
        pay_code_new: str,
        recv_amount_new: Decimal,  # уже квантованные суммы
        pay_amount_new: Decimal,   # уже квантованные суммы
        recv_prec: int,
        pay_prec: int,
        chat_id: int,
        target_bot_msg_id: int,
        cmd_msg_id: int,
        recv_is_deposit: bool,     # как проводится левая нога в этой команде
        pay_is_withdraw: bool,     # как проводится правая нога в этой команде
    ) -> bool:
        """
        Сравнивает «старую» заявку (по тексту) и новые параметры.
        Аккуратно откатывает/доначисляет разницу с идемпотентными ключами.
        Возвращает True, если перерасчёт выполнен; False — если не удалось распарсить старые суммы.
        """
        mg = _RE_GET.search(old_request_text or "")
        mp = _RE_GIVE.search(old_request_text or "")
        if not (mg and mp):
            return False

        parsed_get = _parse_amt_code(mg.group(1))
        parsed_give = _parse_amt_code(mp.group(1))
        if not (parsed_get and parsed_give):
            return False

        old_recv_amt_raw, old_recv_code = parsed_get
        old_pay_amt_raw, old_pay_code = parsed_give

        q_recv = Decimal(10) ** -recv_prec
        q_pay = Decimal(10) ** -pay_prec
        old_recv_amt = old_recv_amt_raw.quantize(q_recv, rounding=ROUND_HALF_UP)
        old_pay_amt = old_pay_amt_raw.quantize(q_pay, rounding=ROUND_HALF_UP)

        idem_prefix = f"edit:{chat_id}:{target_bot_msg_id}:{cmd_msg_id}"

        async def _apply_recv(amount: Decimal, suffix: str) -> None:
            if recv_is_deposit:
                await self.repo.deposit(
                    client_id=client_id, currency_code=recv_code_new, amount=amount,
                    comment=f"edit recv {suffix}", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:{suffix}:recv",
                )
            else:
                await self.repo.withdraw(
                    client_id=client_id, currency_code=recv_code_new, amount=amount,
                    comment=f"edit recv {suffix}", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:{suffix}:recv",
                )

        async def _apply_pay(amount: Decimal, suffix: str) -> None:
            if pay_is_withdraw:
                await self.repo.withdraw(
                    client_id=client_id, currency_code=pay_code_new, amount=amount,
                    comment=f"edit pay {suffix}", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:{suffix}:pay",
                )
            else:
                await self.repo.deposit(
                    client_id=client_id, currency_code=pay_code_new, amount=amount,
                    comment=f"edit pay {suffix}", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:{suffix}:pay",
                )

        # Если валюты поменялись — откатываем старую заявку и применяем новую
        if (old_recv_code != recv_code_new) or (old_pay_code != pay_code_new):
            # Откат старой левой ноги
            if recv_is_deposit:
                await self.repo.withdraw(
                    client_id=client_id, currency_code=old_recv_code, amount=old_recv_amt,
                    comment="edit revert old recv", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:revert:recv",
                )
            else:
                await self.repo.deposit(
                    client_id=client_id, currency_code=old_recv_code, amount=old_recv_amt,
                    comment="edit revert old recv", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:revert:recv",
                )
            # Откат старой правой ноги
            if pay_is_withdraw:
                await self.repo.deposit(
                    client_id=client_id, currency_code=old_pay_code, amount=old_pay_amt,
                    comment="edit revert old pay", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:revert:pay",
                )
            else:
                await self.repo.withdraw(
                    client_id=client_id, currency_code=old_pay_code, amount=old_pay_amt,
                    comment="edit revert old pay", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:revert:pay",
                )
            # Новые суммы
            await _apply_recv(recv_amount_new, "apply")
            await _apply_pay(pay_amount_new, "apply")
            return True

        # Валюты те же — доначисляем дельты
        d_recv = recv_amount_new - old_recv_amt
        d_pay = pay_amount_new - old_pay_amt

        # Левая нога (принимаем)
        if d_recv > 0:
            await _apply_recv(d_recv, "delta+")
        elif d_recv < 0:
            if recv_is_deposit:
                await self.repo.withdraw(
                    client_id=client_id, currency_code=recv_code_new, amount=(-d_recv),
                    comment="edit recv delta-", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:delta-:recv",
                )
            else:
                await self.repo.deposit(
                    client_id=client_id, currency_code=recv_code_new, amount=(-d_recv),
                    comment="edit recv delta-", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:delta-:recv",
                )

        # Правая нога (отдаём)
        if d_pay > 0:
            await _apply_pay(d_pay, "delta+")
        elif d_pay < 0:
            if pay_is_withdraw:
                await self.repo.deposit(
                    client_id=client_id, currency_code=pay_code_new, amount=(-d_pay),
                    comment="edit pay delta-", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:delta-:pay",
                )
            else:
                await self.repo.withdraw(
                    client_id=client_id, currency_code=pay_code_new, amount=(-d_pay),
                    comment="edit pay delta-", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:delta-:pay",
                )

        return True

    # ================================================================
    # Попытка отредактировать существующую заявку (если команда-ответ)
    # Возвращает True, если редактирование выполнено, иначе False.
    # ================================================================
        # utils/exchange_base.py

    async def try_edit_request(
            self,
            *,
            message: Message,
            recv_code: str,
            pay_code: str,
            recv_amount: Decimal,
            pay_amount: Decimal,
            recv_prec: int,
            pay_prec: int,
            rate_str: str,
            user_note: str | None,
            recv_is_deposit: bool,
            pay_is_withdraw: bool,
    ) -> bool:
        reply_msg = getattr(message, "reply_to_message", None)
        if not (reply_msg and (reply_msg.text or "")):
            # Нет реплая — это не редактирование, создаём новую заявку в вызывающем коде
            return False

        # Разрешаем редактирование ТОЛЬКО если ответ на сообщение БОТА с карточкой
        if reply_msg.from_user and reply_msg.from_user.id == message.bot.id:
            mid = _RE_REQ_ID.search(reply_msg.text or "")
            if not mid:
                await message.answer(
                    "Это сообщение бота не похоже на карточку заявки.\n"
                    "Чтобы изменить заявку и пересчитать баланс, ответьте на сообщение БОТА с заявкой."
                )
                return True
            edit_req_id = mid.group(1)
            target_bot_msg_id = reply_msg.message_id
        else:
            # Ответ НЕ боту:
            # 1) Если это ответ на исходную команду (есть линк в индексе) — запрещаем.
            link = req_index.lookup(message.chat.id, reply_msg.message_id)
            if link is not None:
                await message.answer(
                    "Пожалуйста, ответьте на сообщение БОТА с карточкой заявки."
                )
                return True

            # 2) Если выглядит как карточка (пересланная/чужая) — тоже запрещаем.
            if _RE_REQ_ID.search(reply_msg.text or ""):
                await message.answer(
                    "Похоже, вы ответили на пересланную или чужую карточку.\n"
                    "Чтобы изменить заявку и пересчитать баланс, ответьте на оригинальное сообщение БОТА с заявкой."
                )
                return True

            # 3) Любой другой текст — просто инструкция.
            await message.answer(
                "Чтобы изменить заявку и пересчитать баланс, ответьте на сообщение БОТА с карточкой заявки."
            )
            return True

        # Дальше — обычное редактирование существующей карточки бота
        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)

        pretty_recv = format_amount_core(recv_amount, recv_prec)
        pretty_pay = format_amount_core(pay_amount, pay_prec)

        # имя создателя — как раньше
        creator_name: str | None = None
        if reply_msg and reply_msg.text:
            m_created = _RE_CREATED_BY.search(reply_msg.text)
            if m_created:
                creator_name = m_created.group(1).strip()
        if not creator_name:
            u = getattr(message, "from_user", None)
            if u:
                creator_name = (
                        getattr(u, "full_name", None)
                        or (f"@{u.username}" if getattr(u, "username", None) else None)
                        or f"id:{u.id}"
                )
        creator_name = creator_name or "unknown"

        parts_client = [
            f"<b>Заявка</b>: <code>{edit_req_id}</code>",
            "-----",
            f"<b>Получаем</b>: <code>{pretty_recv} {recv_code.lower()}</code>",
            f"<b>Курс</b>: <code>{rate_str}</code>",
            f"<b>Отдаём</b>: <code>{pretty_pay} {pay_code.lower()}</code>",
        ]
        if user_note:
            parts_client += ["----", f"<b>Комментарий</b>: <code>{html.escape(user_note)}</code>"]
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        parts_client += ["----", f"<b>Изменение</b>: <code>{ts}</code>"]
        new_client_text = "\n".join(parts_client)

        did_recalc = False
        try:
            # Есть старый текст карточки бота — применяем дельту с идемпотентностью
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
                recv_is_deposit=recv_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
            )
        except Exception as e:
            await message.answer(f"Не удалось пересчитать балансы: {e}")

        # Обновляем карточку
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=target_bot_msg_id,
                text=new_client_text,
                parse_mode="HTML",
                reply_markup=_cancel_kb(edit_req_id),
            )
        except Exception as e:
            await message.answer(f"Не удалось изменить заявку: {e}")
            return True

        # Дублирование в заявочный чат и показ /дай — как было
        if self.request_chat_id:
            req_lines = [
                f"<b>Заявка</b>: <code>{edit_req_id}</code>",
                f"<b>Клиент</b>: <b>{html.escape(chat_name)}</b>",
                "-----",
                f"<b>Получаем</b>: <code>{pretty_recv} {recv_code.lower()}</code>",
                f"<b>Курс</b>: <code>{rate_str}</code>",
                f"<b>Отдаём</b>: <code>{pretty_pay} {pay_code.lower()}</code>",
            ]
            if user_note:
                req_lines += ["----", f"Комментарий: <code>{html.escape(user_note)}</code> ❗️"]
            req_lines += ["----", f"Изменение: <code>{ts}</code>", "----",
                          f"Создал: <b>{html.escape(creator_name)}</b>"]
            alert_text = "⚠️ Внимание: заявка изменена.\n\n" + "\n".join(req_lines)
            try:
                await post_request_message(message.bot, self.request_chat_id, alert_text, reply_markup=None)
            except Exception:
                pass

        rows = await self.repo.snapshot_wallet(client_id)
        compact = format_wallet_compact(rows, only_nonzero=True)
        if compact == "Пусто":
            await message.answer("Все счета нулевые. Посмотреть всё: /кошелек")
        else:
            safe_title = html.escape(f"Средств у {chat_name}:")
            safe_rows = html.escape(compact)
            await message.answer(f"<code>{safe_title}\n\n{safe_rows}</code>", parse_mode="HTML")

        if not did_recalc:
            await message.answer("ℹ️ Чтобы автоматически пересчитать баланс, отвечайте на сообщение БОТА с заявкой.")
        return True
    # ================================================================
    # Создание новой заявки с проведением двух ног обмена
    # ================================================================
    async def process(
        self,
        message: Message,
        recv_code: str,
        recv_amount_expr: str,
        pay_code: str,
        pay_amount_expr: str,
        *,
        recv_is_deposit: bool = True,
        pay_is_withdraw: bool = True,
        note: str | None = None,
    ) -> None:
        chat_id = message.chat.id
        chat_name = get_chat_name(message)

        RUB_CODES: set[str] = {"RUB", "РУБМСК", "РУБСПБ", "РУБПЕР"}

        # 1) выражения → Decimal
        try:
            recv_amount_raw = evaluate(recv_amount_expr)
            pay_amount_raw = evaluate(pay_amount_expr)
        except CalcError as e:
            await message.answer(f"Ошибка в выражении: {e}")
            return

        if recv_amount_raw <= 0 or pay_amount_raw <= 0:
            await message.answer("Суммы должны быть > 0")
            return

        recv_code = recv_code.strip().upper()
        pay_code = pay_code.strip().upper()

        try:
            # 2) клиент и счета
            client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
            accounts = await self.repo.snapshot_wallet(client_id)

            def _find_acc(code: str):
                return next((r for r in accounts if str(r["currency_code"]).upper() == code), None)

            acc_recv = _find_acc(recv_code)
            acc_pay = _find_acc(pay_code)
            if not acc_recv or not acc_pay:
                missing = recv_code if not acc_recv else pay_code
                await message.answer(
                    f"Счёт {missing} не найден. Добавьте валюту командой: /добавь {missing} [точность]"
                )
                return

            recv_prec = int(acc_recv["precision"])
            pay_prec = int(acc_pay["precision"])

            # 3) квантование
            q_recv = Decimal(10) ** -recv_prec
            q_pay = Decimal(10) ** -pay_prec
            recv_amount = recv_amount_raw.quantize(q_recv, rounding=ROUND_HALF_UP)
            pay_amount = pay_amount_raw.quantize(q_pay, rounding=ROUND_HALF_UP)
            if recv_amount == 0 or pay_amount == 0:
                await message.answer("Сумма слишком мала для точности выбранных валют.")
                return

            # 4) курс
            try:
                # RUB-логика для всех рублёвых кодов (RUB, РУБМСК, РУБСПБ)
                if recv_code in RUB_CODES or pay_code in RUB_CODES:
                    if recv_code in RUB_CODES:
                        rub_raw = recv_amount_raw
                        other_raw = pay_amount_raw
                    else:
                        rub_raw = pay_amount_raw
                        other_raw = recv_amount_raw

                    if other_raw == 0:
                        await message.answer("Курс не определён (деление на ноль).")
                        return

                    auto_rate = rub_raw / other_raw
                else:
                    # обычный случай: курс = pay / recv
                    if recv_amount_raw == 0:
                        await message.answer("Курс не определён (деление на ноль).")
                        return
                    auto_rate = pay_amount_raw / recv_amount_raw

                if not auto_rate.is_finite() or auto_rate <= 0:
                    await message.answer("Курс невалидный.")
                    return

                q_rate = Decimal("1e-8")
                rate_q = auto_rate.quantize(q_rate, rounding=ROUND_HALF_UP)
                rate_str = _fmt_rate(rate_q)
            except (InvalidOperation, ZeroDivisionError):
                await message.answer("Ошибка расчёта курса.")
                return

            # 5) тексты заявок
            req_id = random.randint(10_000_000, 99_999_999)
            pretty_recv = format_amount_core(recv_amount, recv_prec)
            pretty_pay  = format_amount_core(pay_amount,  pay_prec)

            # имя создателя
            creator_name = None
            try:
                u = getattr(message, "from_user", None)
                if u:
                    creator_name = (
                        u.full_name
                        or (f"@{u.username}" if getattr(u, "username", None) else None)
                        or f"id:{u.id}"
                    )
            except Exception:
                pass
            creator_name = creator_name or "unknown"

            base_lines = [
                f"<b>Заявка</b>: <code>{req_id}</code>",
                "-----",
                f"<b>Получаем</b>: <code>{pretty_recv} {recv_code.lower()}</code>",
                f"<b>Курс</b>: <code>{rate_str}</code>",
                f"<b>Отдаём</b>: <code>{pretty_pay} {pay_code.lower()}</code>",
            ]
            if note:
                base_lines += ["----", f"<b>Комментарий</b>: <code>{html.escape(note)}</code>❗️"]
            client_text = "\n".join(base_lines)

            request_lines = [
                f"<b>Заявка</b>: <code>{req_id}</code>",
                f"<b>Клиент</b>: <b>{html.escape(chat_name)}</b>",
                "-----",
                f"<b>Получаем</b>: <code>{pretty_recv} {recv_code.lower()}</code>",
                f"<b>Курс</b>: <code>{rate_str}</code>",
                f"<b>Отдаём</b>: <code>{pretty_pay} {pay_code.lower()}</code>",
            ]
            if note:
                request_lines += ["----", f"<b>Комментарий</b>: <code>{html.escape(note)}</code>❗️"]
            request_lines += [
                "----",
                f"<b>Формула</b>: <code>{html.escape(pay_amount_expr)}</code>",
                "----",
                f"<b>Создал</b>: <b>{html.escape(creator_name)}</b>",
            ]

            request_text = "\n".join(request_lines)

            # 6) проведение операций
            idem_recv = f"{chat_id}:{message.message_id}:recv"
            idem_pay = f"{chat_id}:{message.message_id}:pay"

            recv_comment = recv_amount_expr if not note else f"{recv_amount_expr} | {note}"
            pay_comment  = pay_amount_expr  if not note else f"{pay_amount_expr} | {note}"

            try:
                if recv_is_deposit:
                    await self.repo.deposit(
                        client_id=client_id,
                        currency_code=recv_code,
                        amount=recv_amount,
                        comment=recv_comment,
                        source="exchange",
                        idempotency_key=idem_recv,
                    )
                else:
                    await self.repo.withdraw(
                        client_id=client_id,
                        currency_code=recv_code,
                        amount=recv_amount,
                        comment=recv_comment,
                        source="exchange",
                        idempotency_key=idem_recv,
                    )

                if pay_is_withdraw:
                    await self.repo.withdraw(
                        client_id=client_id,
                        currency_code=pay_code,
                        amount=pay_amount,
                        comment=pay_comment,
                        source="exchange",
                        idempotency_key=idem_pay,
                    )
                else:
                    await self.repo.deposit(
                        client_id=client_id,
                        currency_code=pay_code,
                        amount=pay_amount,
                        comment=pay_comment,
                        source="exchange",
                        idempotency_key=idem_pay,
                    )

            except Exception as leg_err:
                # компенсируем первую ногу (best-effort)
                try:
                    if recv_is_deposit:
                        await self.repo.withdraw(
                            client_id=client_id,
                            currency_code=recv_code,
                            amount=recv_amount,
                            comment="compensate",
                            source="exchange_compensate",
                            idempotency_key=f"{idem_recv}:undo",
                        )
                    else:
                        await self.repo.deposit(
                            client_id=client_id,
                            currency_code=recv_code,
                            amount=recv_amount,
                            comment="compensate",
                            source="exchange_compensate",
                            idempotency_key=f"{idem_recv}:undo",
                        )
                finally:
                    await message.answer(f"Не удалось выполнить обмен: {leg_err}")
                    return

            # 7) клиенту — ответом на исходную команду (тред) + кнопка «Отменить заявку»
            try:
                sent = await message.answer(
                    client_text,
                    parse_mode="HTML",
                    reply_to_message_id=message.message_id,
                    reply_markup=_cancel_kb(req_id),
                )
            except Exception:
                sent = None

            # 7.1) связываем команду и сообщение бота
            if sent is not None:
                try:
                    req_index.remember(
                        chat_id=message.chat.id,
                        user_cmd_msg_id=message.message_id,
                        bot_msg_id=sent.message_id,
                        req_id=str(req_id),
                    )
                except Exception:
                    pass

            # 7.2) заявочный чат — без кнопок
            if self.request_chat_id:
                try:
                    await post_request_message(
                        bot=message.bot,
                        request_chat_id=self.request_chat_id,
                        text=request_text,
                        reply_markup=None,
                    )
                except Exception:
                    pass

            # 8) счета (ненулевые) после операции
            accounts2 = await self.repo.snapshot_wallet(client_id)
            compact = format_wallet_compact(accounts2, only_nonzero=True)
            if compact == "Пусто":
                await message.answer("Все счета нулевые. Посмотреть всё: /кошелек")
            else:
                safe_title = html.escape(f"Средств у {chat_name}:")
                safe_rows = html.escape(compact)
                await message.answer(f"<code>{safe_title}\n\n{safe_rows}</code>", parse_mode="HTML")

        except Exception as e:
            await message.answer(f"Не удалось выполнить операцию: {e}")
