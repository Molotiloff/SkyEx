# handlers/wallets.py
import html
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from keyboards.confirm import rmcur_confirm_kb
from models.wallet import WalletError
from db_asyncpg.repo import Repo
from utils.format_wallet_compact import format_wallet_compact
from utils.calc import evaluate, CalcError
from utils.formatting import format_amount_core, format_amount_with_sign
from utils.info import get_chat_name
from utils.locks import chat_locks
from utils.undos import undo_registry
from utils.auth import (
    require_manager_or_admin_message,
    require_manager_or_admin_callback,
)


def undo_kb(code: str, sign: str, amount_str: str) -> InlineKeyboardMarkup:
    data = f"undo:{code.upper()}:{sign}:{amount_str}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Откатить изменение", callback_data=data)]]
    )


def _normalize_code_alias(raw_code: str) -> str:
    """
    Нормализуем алиасы кодов валют из команды.
    - «РУБ» (кириллица) приводим к стандартному ISO-коду RUB.
    - остальное — UPPER без изменений.
    """
    code_up = (raw_code or "").strip().upper()
    if code_up == "РУБ":
        return "RUB"
    return code_up


class WalletsHandler:
    def __init__(self, repo: Repo, admin_chat_ids=None, admin_user_ids=None) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.router = Router()
        self._register()

    # /кошелек — показать балансы (тоже под гейтом)
    async def _cmd_wallet(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo, message,
            admin_chat_ids=self.admin_chat_ids, admin_user_ids=self.admin_user_ids
        ):
            return

        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id, chat_name)

        rows = await self.repo.snapshot_wallet(client_id)
        safe_title = html.escape(f"Средств у {chat_name}:")
        safe_rows = html.escape(format_wallet_compact(rows, only_nonzero=False))
        await message.answer(f"<code>{safe_title}\n\n{safe_rows}</code>", parse_mode="HTML")

    async def _cmd_rmcur(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo, message,
            admin_chat_ids=self.admin_chat_ids, admin_user_ids=self.admin_user_ids
        ):
            return

        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.answer("Использование: /удали КОД\nПример: /удали CNY")
            return

        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id, chat_name)

        # Нормализуем алиас «РУБ» → RUB
        code = _normalize_code_alias(parts[1])

        accounts = await self.repo.snapshot_wallet(client_id)
        acc = next((r for r in accounts if str(r["currency_code"]).upper() == code), None)
        if not acc:
            await message.answer(f"Счёт {code} не найден.")
            return

        bal = Decimal(str(acc["balance"]))
        prec = int(acc["precision"])
        pretty_bal = format_amount_core(bal, prec)

        warn = ""
        if bal != 0:
            warn = (
                f"\n⚠️ Внимание: баланс по {code} не нулевой ({pretty_bal} {code.lower()}). "
                f"Удаление допустимо — остаток будет потерян."
            )

        await message.answer(
            f"Вы уверены, что хотите удалить валюту {code}?{warn}",
            reply_markup=rmcur_confirm_kb(code)
        )

    # /добавь USD [precision]
    async def _cmd_addcur(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo, message,
            admin_chat_ids=self.admin_chat_ids, admin_user_ids=self.admin_user_ids
        ):
            return

        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id, chat_name)

        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.answer("Использование: /добавь КОД [точность]\nПример: /добавь CNY 2")
            return

        # ВАЖНО: запрещаем отдельную «РУБ», всегда нормализуем в RUB
        code = _normalize_code_alias(parts[1])

        precision = 2
        if len(parts) >= 3:
            try:
                precision = int(parts[2])
            except ValueError:
                await message.answer("Ошибка: точность должна быть целым числом 0..8")
                return

        if not (0 <= precision <= 8):
            await message.answer("Ошибка: точность должна быть в диапазоне 0..8")
            return

        try:
            await self.repo.add_currency(client_id, code, precision=precision)
            await message.answer(f"✅ Валюта {code} добавлена (символов после запятой = {precision})")
        except Exception as e:
            await message.answer(f"Не удалось добавить валюту: {e}")

    # Встроенное изменение баланса: /USD 250  |  /RUB -100  |  /USDT (25+5*3-15/5) | /руб 100
    async def _on_currency_change(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo, message,
            admin_chat_ids=self.admin_chat_ids, admin_user_ids=self.admin_user_ids
        ):
            return

        text = (message.text or "").strip()
        if not text.startswith("/"):
            return

        parts = text[1:].split(None, 1)
        if len(parts) < 2:
            await message.answer(
                "Формат: /КОД ВАЛЮТЫ <сумма/выражение>\n"
                "Примеры: /USD 250, /RUB -100, /USDT (25+5*3-15/5), /руб 1000"
            )
            return

        # Нормализуем «/руб» → RUB
        code = _normalize_code_alias(parts[0])
        expr = parts[1].strip()
        if not expr:
            await message.answer("Сумма не указана. Пример: /USD 250")
            return

        try:
            amount = evaluate(expr)  # Decimal
        except CalcError as e:
            await message.answer(f"Ошибка в выражении суммы: {e}")
            return

        if amount == 0:
            await message.answer("Сумма должна быть ненулевой")
            return

        chat_id = message.chat.id
        chat_name = get_chat_name(message)

        async with chat_locks.for_chat(chat_id):
            try:
                client_id = await self.repo.ensure_client(chat_id, chat_name)
                accounts = await self.repo.snapshot_wallet(client_id)
                acc = next((r for r in accounts if str(r["currency_code"]).upper() == code), None)
                if not acc:
                    await message.answer(
                        f"Счёт {code} не найден.\nПодсказка: добавьте валюту командой /добавь {code} [точность]"
                    )
                    return

                precision = int(acc["precision"])

                q = Decimal(10) ** -precision
                abs_amount = amount.copy_abs()
                delta_quant = abs_amount.quantize(q, rounding=ROUND_HALF_UP)

                if delta_quant == 0:
                    min_step = format_amount_core(q, precision)
                    await message.answer(
                        f"Сумма слишком мала для точности {precision}.\n"
                        f"Минимальный шаг для {code.upper()}: {min_step} {code.lower()}"
                    )
                    return

                idem = f"{chat_id}:{message.message_id}"
                if amount > 0:
                    await self.repo.deposit(
                        client_id=client_id,
                        currency_code=code,
                        amount=delta_quant,
                        comment=expr,
                        source="command",
                        idempotency_key=idem,
                    )
                    sign_flag = "+"
                    pretty_amt = format_amount_with_sign(delta_quant, precision, sign="+")
                else:
                    await self.repo.withdraw(
                        client_id=client_id,
                        currency_code=code,
                        amount=delta_quant,
                        comment=expr,
                        source="command",
                        idempotency_key=idem,
                    )
                    sign_flag = "-"
                    pretty_amt = format_amount_with_sign(delta_quant, precision, sign="-")

                accounts2 = await self.repo.snapshot_wallet(client_id)
                acc2 = next((r for r in accounts2 if str(r["currency_code"]).upper() == code), None)
                cur_bal = Decimal(str(acc2["balance"])) if acc2 else Decimal("0")
                pretty_bal = format_amount_core(cur_bal, precision)

                amt_str = f"{delta_quant}"
                text_out = f"Запомнил. {pretty_amt}\nБаланс: {pretty_bal} {code.lower()}"
                await message.answer(text_out, reply_markup=undo_kb(code, sign_flag, amt_str))

            except WalletError as we:
                await message.answer(f"Ошибка: {we}")
            except Exception as e:
                await message.answer(f"Не удалось обработать операцию: {e}")

    # обработка подтверждения/отмены удаления
    async def _cb_rmcur(self, cq: CallbackQuery) -> None:
        if not await require_manager_or_admin_callback(
            self.repo, cq,
            admin_chat_ids=self.admin_chat_ids, admin_user_ids=self.admin_user_ids
        ):
            return

        try:
            _, code_raw, answer = (cq.data or "").split(":")
        except Exception:
            await cq.answer("Некорректные данные", show_alert=True)
            return

        code = _normalize_code_alias(code_raw)  # нормализуем «РУБ» → RUB
        chat_id = cq.message.chat.id if cq.message else None
        if chat_id is None:
            await cq.answer("Нет чата", show_alert=True)
            return

        chat_name = cq.message.chat.title if cq.message and cq.message.chat else ""
        client_id = await self.repo.ensure_client(chat_id, chat_name)

        if answer == "no":
            await cq.message.edit_text(f"Удаление {code} отменено.")
            await cq.answer("Отмена")
            return

        try:
            # Разрешено удаление независимо от баланса
            ok = await self.repo.remove_currency(client_id, code)
            if ok:
                await cq.message.edit_text(f"🗑 Валюта {code} удалена из кошелька.")
                await cq.answer("Удалено")
            else:
                await cq.message.edit_text(f"Не удалось удалить {code}: счёт не найден или уже отключён.")
                await cq.answer("Отклонено", show_alert=True)
        except Exception as e:
            await cq.message.edit_text(f"Ошибка удаления {code}: {e}")
            await cq.answer("Ошибка", show_alert=True)

    async def _cb_undo(self, cq: CallbackQuery) -> None:
        if not await require_manager_or_admin_callback(
            self.repo, cq,
            admin_chat_ids=self.admin_chat_ids, admin_user_ids=self.admin_user_ids
        ):
            return

        # ожидаем data формата: "undo:CODE:SIGN:AMOUNT"
        try:
            kind, code_raw, sign, amt_str = (cq.data or "").split(":")
            if kind != "undo":
                return
        except Exception:
            await cq.answer("Некорректные данные", show_alert=True)
            return

        if not cq.message:
            await cq.answer("Нет сообщения", show_alert=True)
            return

        code = _normalize_code_alias(code_raw)
        chat_id = cq.message.chat.id
        msg_id = cq.message.message_id
        key = (chat_id, msg_id)

        async with chat_locks.for_chat(chat_id):
            if await undo_registry.is_done(key):
                await cq.answer("Операция уже отменена")
                try:
                    await cq.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
                # Показать балансы даже если уже отменяли (на всякий случай)
                try:
                    chat_name = get_chat_name(cq.message)
                    client_id = await self.repo.ensure_client(chat_id, chat_name)
                    rows = await self.repo.snapshot_wallet(client_id)
                    compact = format_wallet_compact(rows, only_nonzero=True)
                    if compact == "Пусто":
                        await cq.message.answer("Все счета нулевые. Посмотреть всё: /кошелек")
                    else:
                        safe_title = html.escape(f"Средств у {chat_name}:")
                        safe_rows = html.escape(compact)
                        await cq.message.answer(f"<code>{safe_title}\n\n{safe_rows}</code>", parse_mode="HTML")
                except Exception:
                    pass
                return

            try:
                amount = Decimal(amt_str)
            except InvalidOperation:
                await cq.answer("Ошибка суммы", show_alert=True)
                return

            try:
                chat_name = get_chat_name(cq.message)
                client_id = await self.repo.ensure_client(chat_id, chat_name)

                # Инверсная операция
                if sign == "+":
                    await self.repo.withdraw(
                        client_id=client_id,
                        currency_code=code,
                        amount=amount,
                        comment="undo",
                        source="undo",
                        idempotency_key=f"undo:{chat_id}:{msg_id}",
                    )
                elif sign == "-":
                    await self.repo.deposit(
                        client_id=client_id,
                        currency_code=code,
                        amount=amount,
                        comment="undo",
                        source="undo",
                        idempotency_key=f"undo:{chat_id}:{msg_id}",
                    )
                else:
                    await cq.answer("Некорректный знак", show_alert=True)
                    return

                await undo_registry.mark_done(key)

                # Обновляем текст исходного сообщения и убираем клавиатуру
                try:
                    old_text = cq.message.text or ""
                    new_text = old_text + "\n↩️ Отменено."
                    await cq.message.edit_text(new_text)
                except Exception:
                    pass
                try:
                    await cq.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass

                # Показать актуальные балансы
                try:
                    rows = await self.repo.snapshot_wallet(client_id)
                    compact = format_wallet_compact(rows, only_nonzero=True)
                    if compact == "Пусто":
                        await cq.message.answer("Все счета нулевые. Посмотреть всё: /кошелек")
                    else:
                        safe_title = html.escape(f"Средств у {chat_name}:")
                        safe_rows = html.escape(compact)
                        await cq.message.answer(f"<code>{safe_title}\n\n{safe_rows}</code>", parse_mode="HTML")
                except Exception as e:
                    await cq.message.answer(f"Не удалось показать балансы: {e}")

                await cq.answer("Откат выполнен")
            except Exception as e:
                await cq.answer(f"Сбой: {e}", show_alert=True)

    def _register(self) -> None:
        self.router.message.register(self._cmd_wallet, Command("кошелек"))
        self.router.message.register(self._cmd_addcur, Command("добавь"))
        self.router.message.register(self._cmd_rmcur, Command("удали"))

        # Встроенная смена баланса: "/USD <expr>" (поддерживает и "/руб <expr>")
        self.router.message.register(
            self._on_currency_change,
            F.text.regexp(r"^/[^\W\d_]+\s+")
        )

        # подтверждение удаления и undo
        self.router.callback_query.register(self._cb_rmcur, F.data.startswith("rmcur:"))
        self.router.callback_query.register(self._cb_undo, F.data.startswith("undo:"))
