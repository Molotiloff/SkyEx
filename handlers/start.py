# handlers/start.py
from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery

from keyboards.main import MainKeyboard
from db_asyncpg.repo import Repo
from utils.info import get_chat_name
from utils.wallet_bootstrap import ensure_default_accounts  # ← хелпер автосоздания валют


class StartHandler:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo
        self.router = Router()
        self._register()

    async def _on_start(self, message: Message) -> None:
        # регистрируем/обновляем клиента (чат) в БД
        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)

        # добавляем базовые валюты, если кошелёк пуст
        await ensure_default_accounts(self.repo, client_id)

        text = (
            "👋 Привет! Я бот обменника SkyEx.\n\n"
            "📌 Основные команды:\n"
            "• <code>/помоги</code> — справка по функциям\n"
            "• <code>/кошелек</code> — все счета клиента\n"
            "• <code>/дай</code> — только ненулевые счета\n"
            "• <code>/кош</code> — USDT-кошелёк для приёма\n\n"

            "💵 Балансы:\n"
            "• <code>/добавь ВАЛЮТА [точность]</code> — добавить валюту\n"
            "• <code>/удали ВАЛЮТА</code> — удалить валюту\n"
            "• <code>/USD 250</code> — изменить баланс по счёту (можно выражением)\n\n"

            "🔁 Обмен:\n"
            "• Короткие команды: <code>/пд</code>, <code>/пе</code>, <code>/пт</code>, <code>/пр</code>, "
            "<code>/пб</code>\n"
            "+ <code>од</code>, <code>ое</code>, <code>от</code>, <code>ор</code>, <code>об</code> — оформить обмен\n\n"

            "🧾 Заявки наличными:\n"
            "• <code>/деп…</code> — депозит\n"
            "• <code>/выд…</code> — выдача клиенту\n\n"

            "🖼 Прочее:\n"
            "• <code>/проходка</code> — пропуск и инструкция к офису/паркингу\n"
        )
        await message.answer(text, reply_markup=MainKeyboard.main(), parse_mode="HTML")

    async def _show_help(self, message: Message) -> None:
        text = (
            "📖 Доступные команды\n\n"

            "🏦 Кошелёк:\n"
            "• <code>/кошелек</code> — показать все счета.\n"
            "• <code>/дай</code> — показать только ненулевые счета.\n"
            "• <code>/добавь ВАЛЮТА [точность]</code> — добавить валюту (напр. <code>/добавь CNY 2</code>).\n"
            "• <code>/удали ВАЛЮТА</code> — удалить валюту.\n"
            "• <code>/кош</code> — показать USDT-кошелёк для приёма.\n"
            "  Изменение адреса — только в админском чате.\n\n"

            "💵 Изменение баланса (по счёту):\n"
            "• <code>/USD 250</code> — добавить 250 USD\n"
            "• <code>/RUB -100</code> — списать 100 RUB (овердрафт разрешён)\n"
            "• <code>/USDT (2+3*4)</code> — изменить на результат выражения\n\n"

            "🔁 Обмен:\n"
            "• Принимаем: <code>/пд</code> (USD), <code>/пе</code> (EUR), "
            "<code>/пт</code> (USDT), <code>/пр</code> (RUB)\n"
            "• Отдаём: <code>од</code> (USD), <code>ое</code> (EUR), "
            "<code>от</code> (USDT), <code>ор</code> (RUB)\n"
            "Примеры:\n"
            "• <code>/пд 1000 ое 1000/0.92 Клиент Петров</code>\n"
            "• <code>/пр 100000 от 100000/97 срочно</code>\n\n"

            "🧾 Заявки наличными:\n"
            "• Депозит (клиент приносит): <code>/депр</code> (RUB), <code>/депт</code> (USDT), "
            "<code>/депд</code> (USD), <code>/депе</code> (EUR)\n"
            "• Выдача (клиенту выдаём): <code>/выдр</code> (RUB), <code>/выдт</code> (USDT), "
            "<code>/выдд</code> (USD), <code>/выде</code> (EUR)\n\n"

            "🗂 Админский чат:\n"
            "• <code>/заявка - Проводит обмен от имени админского чата\n\n"

            "📊 Отчёты:\n"
            "• <code>/бк</code> — балансы клиентов по всем валютам (только ненулевые).\n"
            "• <code>/бк &lt;ВАЛЮТА&gt; &lt;+|-&gt;</code> — фильтр: только положительные/отрицательные "
            "(напр. <code>/бк EUR -</code>).\n\n"

            "👥 Клиенты и города:\n"
            "• <code>/клиенты</code> — список клиентов (название, chat_id, город).\n"
            "• <code>/город &lt;chat_id&gt; &lt;город&gt;</code> — присвоить город клиенту (только админам).\n\n"

            "🔐 Роли и доступ:\n"
            "• Команды обмена и заявок — только для менеджеров и админов.\n"
            "• Управление менеджерами — в админском чате.\n\n"

            "🖼 Прочее:\n"
            "• <code>/проходка</code> — пропуск и инструкции по офису/паркингу.\n"
        )

        await message.answer(text, reply_markup=MainKeyboard.main(), parse_mode="HTML")

    async def _cb_menu_help(self, cq: CallbackQuery) -> None:
        if cq.message:
            await self._show_help(cq.message)
        await cq.answer()

    async def _cmd_help(self, message: Message) -> None:
        await self._show_help(message)

    def _register(self) -> None:
        self.router.message.register(self._on_start, CommandStart())
        self.router.message.register(self._cmd_help, Command("помоги"))
