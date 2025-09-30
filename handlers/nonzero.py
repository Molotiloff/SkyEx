# handlers/nonzero.py
import html
import re
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from db_asyncpg.repo import Repo
from utils.format_wallet_compact import format_wallet_compact
from utils.info import get_chat_name

# выписки (общий модуль)
from utils.statements import statements_kb, handle_stmt_callback


# Человеческие названия валют для отображения
_DISPLAY_NAMES_RU = {
    "USD": "дол",
    "USDT": "юсдт",
    "EUR": "евро",
    "RUB": "руб",
    "USDW": "долб",   # «доллар белый»
}

# Алиасы валют (латиница/кириллица) → нормальный код
_CURRENCY_ALIASES = {
    # USD
    "usd": "USD", "дол": "USD", "долл": "USD", "доллар": "USD", "доллары": "USD",
    # USDT
    "usdt": "USDT", "юсдт": "USDT",
    # EUR
    "eur": "EUR", "евро": "EUR",
    # RUB
    "rub": "RUB", "руб": "RUB", "рубль": "RUB", "рубли": "RUB", "рублей": "RUB", "руб.": "RUB",
    # USDW
    "usdw": "USDW", "долб": "USDW", "доллбел": "USDW", "долбел": "USDW",
}


def _display_code_ru(code: str) -> str:
    """Вернуть человеко-читаемое имя валюты, если есть, иначе сам код в нижнем регистре."""
    return _DISPLAY_NAMES_RU.get(code.upper(), code).lower()


def _normalize_code_alias(raw: str) -> str:
    """Нормализуем алиасы валют: /дол → USD, /юсдт → USDT и т.п."""
    key = (raw or "").strip().lower()
    return _CURRENCY_ALIASES.get(key, (raw or "").strip().upper())


class NonZeroHandler:
    """
    /дай — показать все счета с ненулевым балансом (включая отрицательные),
           в «компактном» формате (все валюты отображаются по-русски).
    /дай <валюта> — показать баланс по конкретной валюте (даже если он нулевой),
           с тем же компактным выравниванием.
    Под ответом — кнопки «Выписка за месяц» и «Выписка за всё время».
    """
    def __init__(self, repo: Repo, admin_chat_ids=None, admin_user_ids=None) -> None:
        self.repo = repo
        self.router = Router()
        self._register()

    async def _cmd_give(self, message: Message) -> None:
        text = (message.text or "").strip()
        # парсим аргумент валюты, если он передан
        m = re.match(r"(?iu)^/дай(?:@\w+)?(?:\s+(\S+))?\s*$", text)
        arg_code = m.group(1) if m else None

        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id, chat_name)

        rows = await self.repo.snapshot_wallet(client_id)
        if not rows:
            await message.answer(
                "Нет счетов. Добавьте валюту: /добавь USD 2",
                reply_markup=statements_kb(),  # всё равно даём быстрый доступ к выпискам (на случай истории)
            )
            return

        # Если указан код валюты — показываем только её баланс (через compact для выравнивания)
        if arg_code:
            code = _normalize_code_alias(arg_code)
            acc = next((r for r in rows if str(r["currency_code"]).upper() == code), None)
            if not acc:
                await message.answer(
                    f"Счёт {code} не найден. Добавьте валюту: /добавь {code} [точность]",
                    reply_markup=statements_kb(),
                )
                return

            # готовим одну строку для компакта
            prec = int(acc.get("precision", 2))
            bal = acc.get("balance")
            single_row = [{
                "currency_code": _display_code_ru(code),  # человеко-читаемое имя, как в общем режиме
                "balance": bal,
                "precision": prec,
            }]
            compact_one = format_wallet_compact(single_row, only_nonzero=False)

            safe_title = html.escape(f"Средств у {chat_name}:")
            safe_rows = html.escape(compact_one)
            await message.answer(
                f"<code>{safe_title}\n\n{safe_rows}</code>",
                parse_mode="HTML",
                reply_markup=statements_kb(),
            )
            return

        # Иначе — режим «все ненулевые»
        # заменим коды валют на человеко-читаемые
        renamed = []
        for r in rows:
            r2 = dict(r)
            r2["currency_code"] = _display_code_ru(str(r["currency_code"]))
            renamed.append(r2)

        compact = format_wallet_compact(renamed, only_nonzero=True)
        if compact == "Пусто":
            await message.answer(
                "Все счета нулевые. Посмотреть всё: /кошелек",
                reply_markup=statements_kb(),
            )
            return

        safe_title = html.escape(f"Средств у {chat_name}:")
        safe_rows = html.escape(compact)
        await message.answer(
            f"<code>{safe_title}\n\n{safe_rows}</code>",
            parse_mode="HTML",
            reply_markup=statements_kb(),
        )

    async def _cb_statement(self, cq: CallbackQuery) -> None:
        # делегируем общий обработчик выписок
        await handle_stmt_callback(cq, self.repo)

    def _register(self) -> None:
        self.router.message.register(self._cmd_give, Command("дай"))
        # поддержка формы с аргументом по regex
        self.router.message.register(
            self._cmd_give,
            F.text.regexp(r"(?iu)^/дай(?:@\w+)?(?:\s+\S+)?\s*$"),
        )
        # обработка коллбэков выписок
        self.router.callback_query.register(self._cb_statement, F.data.in_({"stmt:month", "stmt:all"}))