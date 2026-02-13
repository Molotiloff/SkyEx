# handlers/wallets.py
import html
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Iterable

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from db_asyncpg.repo import Repo
from keyboards.confirm import rmcur_confirm_kb
from models.wallet import WalletError
from utils.auth import (
    require_manager_or_admin_message,
    require_manager_or_admin_callback,
)
from utils.calc import evaluate, CalcError
from utils.city_cash_transfer import city_cash_transfer_to_client
from utils.format_wallet_compact import format_wallet_compact
from utils.formatting import format_amount_core, format_amount_with_sign
from utils.info import get_chat_name
from utils.locks import chat_locks
from utils.statements import statements_kb, handle_stmt_callback
from utils.undos import undo_registry


def undo_kb(code: str, sign: str, amount_str: str) -> InlineKeyboardMarkup:
    data = f"undo:{code.upper()}:{sign}:{amount_str}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Откатить изменение", callback_data=data)]]
    )


# ---- Алиасы кодов валют
_CURRENCY_ALIASES = {
    "usd": "USD", "дол": "USD", "долл": "USD", "доллар": "USD", "доллары": "USD",
    "usdt": "USDT", "юсдт": "USDT",
    "eur": "EUR", "евро": "EUR",
    "rub": "RUB", "руб": "RUB", "рубль": "RUB", "рубли": "RUB", "рублей": "RUB", "руб.": "RUB", "рубль.": "RUB",
    "usdw": "USDW", "долб": "USDW", "доллбел": "USDW", "долбел": "USDW",
}

_ALLOWED_EXPR_CHARS = r"0-9\.\,\+\-\*\/\(\)\s%"


def _extract_expr_prefix(s: str) -> str:
    if not s:
        return ""
    first = s.strip().split(maxsplit=1)[0]
    return first.replace(",", ".")


def _normalize_code_alias(raw_code: str) -> str:
    key = (raw_code or "").strip().lower()
    alias = _CURRENCY_ALIASES.get(key)
    if alias:
        return alias
    if key in ("руб", "рубль", "рубли", "рублей", "руб."):
        return "RUB"
    return (raw_code or "").strip().upper()


def _split_city_transfer_tail(tail: str) -> tuple[str, str]:
    """
    tail = '<client_name> [! comment]'
    Возвращает (client_name, comment)
    """
    s = (tail or "").strip()
    if not s:
        return "", ""
    # комментарий отделяем "!"
    left, sep, right = s.partition("!")
    client_name = left.strip()
    comment = right.strip() if sep else ""
    return client_name, comment


class WalletsHandler:
    def __init__(
            self,
            repo: Repo,
            admin_chat_ids: Iterable[int] | None = None,
            admin_user_ids: Iterable[int] | None = None,
            *,
            ignore_chat_ids: Iterable[int] | None = None,
            city_cash_chat_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.ignore_chat_ids = set(ignore_chat_ids or [])
        self.city_cash_chat_ids = set(city_cash_chat_ids or [])
        self.router = Router()
        self._register()

    # /кошелек — показать балансы (доступ только менеджерам/админам) + кнопки выписок
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
        await message.answer(
            f"<code>{safe_title}\n\n{safe_rows}</code>",
            parse_mode="HTML",
            reply_markup=statements_kb(),
        )

    async def _cmd_rmcur(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo, message,
            admin_chat_ids=self.admin_chat_ids, admin_user_ids=self.admin_user_ids
        ):
            return

        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.answer("Использование: /удали КОД\nПримеры: /удали USD, /удали дол, /удали юсдт")
            return

        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id, chat_name)

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
            await message.answer(
                "Использование: /добавь КОД [точность]\n"
                "Примеры: /добавь USD 2, /добавь дол 2, /добавь юсдт 0, /добавь доллбел 2"
            )
            return

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

    # Изменение баланса: "/USD <expr>

    async def _on_currency_change(self, message: Message) -> None:
        # 0) не обрабатываем валютные "команды", которые бот сам себе отправил (важно для пересылки в чат клиента)
        if message.from_user and message.bot and message.from_user.id == message.bot.id:
            return

        # --- НОВОЕ: глушим валютные команды в «заявочных» (или иных) чатах ---
        if message.chat and message.chat.id in self.ignore_chat_ids:
            return

        if not await require_manager_or_admin_message(
                self.repo, message,
                admin_chat_ids=self.admin_chat_ids, admin_user_ids=self.admin_user_ids
        ):
            return

        text = (message.text or message.caption or "").strip()
        if not text.startswith("/"):
            return

        parts = text[1:].split(None, 1)
        if len(parts) < 2:
            await message.answer(
                "Формат: /КОД ВАЛЮТЫ <сумма/выражение> [комментарий]\n"
                "Примеры: /руб 1000, /usd 250, /руб 1000 от сани\n\n"
                "Для кассы города:\n"
                "• /руб 10000 <точное имя клиента из строки 'Клиент:'> [! комментарий]"
            )
            return

        raw_code = parts[0]
        code = _normalize_code_alias(raw_code)

        expr_full = parts[1].strip()
        expr = _extract_expr_prefix(expr_full)
        if not expr:
            await message.answer("Сумма не указана. Пример: /USD 250")
            return

        # хвост после expr (там будет либо обычный комментарий, либо 'client_name [! comment]' для кассы)
        first_token = expr_full.strip().split(maxsplit=1)[0]
        tail = expr_full[len(first_token):].strip()

        try:
            amount = evaluate(expr)  # Decimal (со знаком)
        except CalcError as e:
            await message.answer(f"Ошибка в выражении суммы: {e}")
            return

        if amount == 0:
            await message.answer("Сумма должна быть ненулевой")
            return

        chat_id = message.chat.id
        chat_name = get_chat_name(message)

        is_city_cash = chat_id in self.city_cash_chat_ids
        client_name_for_transfer = ""
        extra_comment = ""

        if is_city_cash:
            # tail => "<точное имя клиента> [! comment]"
            client_name_for_transfer, extra_comment = _split_city_transfer_tail(tail)
        else:
            extra_comment = tail

        async with chat_locks.for_chat(chat_id):
            try:
                # 1) применяем операцию в текущем чате (как раньше)
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
                delta_quant = amount.copy_abs().quantize(q, rounding=ROUND_HALF_UP)

                if delta_quant == 0:
                    min_step = format_amount_core(q, precision)
                    await message.answer(
                        f"Сумма слишком мала для точности {precision}.\n"
                        f"Минимальный шаг для {code.upper()}: {min_step} {code.lower()}"
                    )
                    return

                idem = f"{chat_id}:{message.message_id}"
                comment_for_txn = expr if not extra_comment else f"{expr} | {extra_comment}"

                if amount > 0:
                    await self.repo.deposit(
                        client_id=client_id,
                        currency_code=code,
                        amount=delta_quant,
                        comment=comment_for_txn,
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
                        comment=comment_for_txn,
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
                await message.answer(
                    f"Запомнил. {pretty_amt}\nБаланс: {pretty_bal} {code.lower()}",
                    reply_markup=undo_kb(code, sign_flag, amt_str),
                )

                # 2) НОВОЕ: касса города → дублируем операцию + фото в чат клиента + показываем новый баланс клиента
                if is_city_cash and client_name_for_transfer:
                    res = await city_cash_transfer_to_client(
                        repo=self.repo,
                        bot=message.bot,
                        src_message=message,
                        currency_code=code,
                        amount_signed=amount,  # ✅ со знаком (+/-), хелпер сам квантит под точность клиента
                        amount_expr=expr,  # ✅ ровно expr (без хвоста)
                        client_name_exact=client_name_for_transfer,
                        extra_comment=extra_comment,
                    )
                    if not res.ok:
                        await message.answer(res.error or "⚠️ Не удалось продублировать операцию в чат клиента.")
                        return

                    await message.answer("✅ Продублировал в чат клиента (фото + операция + новый баланс).")

            except WalletError as we:
                await message.answer(f"Ошибка: {we}")
            except Exception as e:
                await message.answer(f"Не удалось обработать операцию: {e}")

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

        code = _normalize_code_alias(code_raw)
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
                try:
                    chat_name = get_chat_name(cq.message)
                    client_id = await self.repo.ensure_client(chat_id, chat_name)
                    rows = await self.repo.snapshot_wallet(client_id)
                    acc = next((r for r in rows if str(r["currency_code"]).upper() == code), None)
                    if acc:
                        precision = int(acc["precision"])
                        cur_bal = Decimal(str(acc["balance"]))
                        pretty_bal = format_amount_core(cur_bal, precision)
                        await cq.message.answer(
                            f"Баланс: {pretty_bal} {code.lower()}",
                            parse_mode="HTML"
                        )
                    else:
                        await cq.message.answer(f"Счёт {code} не найден.")
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

                if sign == "+":
                    await self.repo.withdraw(
                        client_id=client_id,
                        currency_code=code,
                        amount=amount,
                        comment="undo",
                        source="undo",
                        idempotency_key=f"undo:{chat_id}:{msg_id}",
                    )
                    applied_sign = "-"
                elif sign == "-":
                    await self.repo.deposit(
                        client_id=client_id,
                        currency_code=code,
                        amount=amount,
                        comment="undo",
                        source="undo",
                        idempotency_key=f"undo:{chat_id}:{msg_id}",
                    )
                    applied_sign = "+"
                else:
                    await cq.answer("Некорректный знак", show_alert=True)
                    return

                await undo_registry.mark_done(key)

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

                try:
                    rows = await self.repo.snapshot_wallet(client_id)
                    acc = next((r for r in rows if str(r["currency_code"]).upper() == code), None)
                    if acc:
                        precision = int(acc["precision"])
                        cur_bal = Decimal(str(acc["balance"]))
                        pretty_bal = format_amount_core(cur_bal, precision)
                        pretty_delta = format_amount_with_sign(amount, precision, sign=applied_sign)
                        await cq.message.answer(
                            f"Запомнил. {pretty_delta}\nБаланс: {pretty_bal} {code.lower()}",
                            parse_mode="HTML"
                        )
                    else:
                        await cq.message.answer(f"Счёт {code} не найден.")
                except Exception as e:
                    await cq.message.answer(f"Не удалось показать баланс по {code}: {e}")

                await cq.answer("Откат выполнен")
            except Exception as e:
                await cq.answer(f"Сбой: {e}", show_alert=True)

    async def _cb_statement(self, cq: CallbackQuery) -> None:
        # просто делегируем в общий обработчик
        await handle_stmt_callback(cq, self.repo)

    def _register(self) -> None:
        self.router.message.register(self._cmd_wallet, Command("кошелек"))
        self.router.message.register(self._cmd_addcur, Command("добавь"))
        self.router.message.register(self._cmd_rmcur, Command("удали"))

        # Валютные команды (игнор в указанных чатах делаем внутри обработчика)
        self.router.message.register(
            self._on_currency_change,
            F.text.regexp(r"^/[^\W\d_]+\s+")
        )

        self.router.message.register(
            self._on_currency_change,
            F.caption.regexp(r"^/[^\W\d_]+\s+")
        )

        self.router.callback_query.register(self._cb_rmcur, F.data.startswith("rmcur:"))
        self.router.callback_query.register(self._cb_undo, F.data.startswith("undo:"))
        self.router.callback_query.register(self._cb_statement, F.data.in_({"stmt:month", "stmt:all"}))