# handlers/start.py
from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery

from keyboards.main import MainKeyboard
from db_asyncpg.repo import Repo
from utils.info import get_chat_name
from utils.wallet_bootstrap import ensure_default_accounts  # ← хелпер автосоздания валют

text_help = (
            "📖 Доступные команды\n\n"

            "🏦 Кошелёк:\n"
            "• <code>/кошелек</code> — показать все счета.\n"
            "• <code>/дай</code> — показать только ненулевые счета.\n"
            "• <code>/добавь ВАЛЮТА [точность]</code> — добавить валюту (напр. <code>/добавь CNY 2</code>).\n"
            "• <code>/удали ВАЛЮТА</code> — удалить валюту.\n\n"

            "💵 Изменение баланса (по счёту):\n"
            "• <code>/USD 250</code> — добавить 250 USD\n"
            "• <code>/RUB -100</code> — списать 100 RUB (овердрафт разрешён)\n"
            "• <code>/USDT (2+3*4)</code> — изменить на результат выражения\n\n"

            "🔁 Обмен (короткие команды):\n"
            "• Принимаем (списываем у клиента): <code>/пд</code> (USD), <code>/пе</code> (EUR), "
            "<code>/пт</code> (USDT), <code>/пр</code> (RUB)\n"
            "• Отдаём (зачисляем клиенту): <code>од</code> (USD), <code>ое</code> (EUR), "
            "<code>от</code> (USDT), <code>ор</code> (RUB)\n"
            "Примеры:\n"
            "• <code>/пд 1000 ор 1000*84 Клиент Петров</code>\n"
            "• <code>/пр 100000 от 100000/97 срочно</code>\n\n"

            "🧾 Заявки наличными:\n"
            "• Депозит (клиент приносит): <code>/депр</code> (RUB), <code>/депт</code> (USDT), "
            "<code>/депд</code> (USD), <code>/депе</code> (EUR), <code>/депб</code> (USDW)\n"
            "• Выдача (клиенту выдаём): <code>/выдр</code> (RUB), <code>/выдт</code> (USDT), "
            "<code>/выдд</code> (USD), <code>/выде</code> (EUR), <code>/выдб</code> (USDW)\n\n"

            "📊 Отчёты:\n"
            "• <code>/бк</code> — балансы клиентов по всем валютам (только ненулевые).\n"
            "• <code>/бк &lt;ВАЛЮТА&gt; &lt;+|-&gt;</code> — фильтр: только положительные/отрицательные по валюте "
            "(напр. <code>/бк EUR -</code>).\n\n"

            "👥 Клиенты и города:\n"
            "• <code>/клиенты</code> — список клиентов (название, chat_id, город).\n"
            "• <code>/город &lt;chat_id&gt; &lt;город&gt;</code> — присвоить город клиенту (только админам).\n\n"

            "🔐 Роли и доступ:\n"
            "• Команды обмена и заявок доступны менеджерам и админам.\n"
            "• Добавление/удаление менеджеров выполняется в админском чате.\n\n"

            "💼 Кошелёк USDT:\n"
            "• <code>/кош</code> — показать адрес кошелька USDT (TRC20). "
            "Изменение адреса — только в админском чате.\n\n"

            "🖼 Прочее:\n"
            "• <code>/проходка</code> — пропуск и инструкции по офису/паркингу.\n"
        )

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
            "👋 Привет! Я бот обменника <b>SkyEx</b>.\n\n"

            "📜 <b>Условия работы:</b>\n"
            "1️⃣ Перед отправкой USDT всегда уточняйте актуальный кошелёк командой <code>/кош</code>\n"
            "2️⃣ Покупка/продажа USDT всегда подтверждается ФИКСом.\n"
            "   В случае срыва фикса в некоторых случаях взимается неустойка "
            "(если мы понесли потери на фоне роста/падения курса или иных обстоятельств).\n"
            "3️⃣ Офис работает с 10:00 до 22:00, далее — по согласованию.\n"
            "4️⃣ В чате с клиентом ведётся SkyEx Bot — он нужен для нашей бухгалтерии.\n"
        )
        await message.answer(text, reply_markup=MainKeyboard.main(), parse_mode="HTML")

    async def _show_help(self, message: Message) -> None:
        text = (
            "👋 Привет! Я бот обменника <b>SkyEx</b>.\n\n"

            "📜 <b>Условия работы:</b>\n"
            "1️⃣ Перед отправкой USDT всегда уточняйте актуальный кошелёк командой <code>/кош</code>\n"
            "2️⃣ Покупка/продажа USDT всегда подтверждается ФИКСом.\n"
            "   В случае срыва фикса в некоторых случаях взимается неустойка "
            "(если мы понесли потери на фоне роста/падения курса или иных обстоятельств).\n"
            "3️⃣ Офис работает с 10:00 до 22:00, далее — по согласованию.\n"
            "4️⃣ В чате с клиентом ведётся SkyEx Bot — он нужен для нашей бухгалтерии.\n"
            "5️⃣ Принимаем/выдаём USD номиналом 100. "
            "Если у вас другой номинал — предупреждайте.\n"
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
