# handlers/nonzero.py
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from services.admin_client import NonZeroWalletQueryService

# выписки (общий модуль)
from utils.statements import statements_kb


class NonZeroHandler:
    """
    /дай — показать все счета с ненулевым балансом (включая отрицательные),
           в «компактном» формате (все валюты отображаются по-русски).
    /дай <валюта> — показать баланс по конкретной валюте (даже если он нулевой),
           с тем же компактным выравниванием.
    Под ответом — кнопки «Выписка за месяц» и «Выписка за всё время».
    """
    def __init__(self, service: NonZeroWalletQueryService, admin_chat_ids=None, admin_user_ids=None) -> None:
        self.service = service
        self.router = Router()
        self._register()

    async def _cmd_give(self, message: Message) -> None:
        await message.answer(
            await self.service.build_wallet_message(
                command_text=message.text or "",
                chat_id=message.chat.id,
                chat_name=self.service.chat_name_from_message(message),
            ),
            parse_mode="HTML",
            reply_markup=statements_kb(),
        )

    async def _cb_statement(self, cq: CallbackQuery) -> None:
        # делегируем общий обработчик выписок
        await self.service.handle_statement_callback(cq)

    def _register(self) -> None:
        self.router.message.register(self._cmd_give, Command("дай"))
        # поддержка формы с аргументом по regex
        self.router.message.register(
            self._cmd_give,
            F.text.regexp(r"(?iu)^/дай(?:@\w+)?(?:\s+\S+)?\s*$"),
        )
        # обработка коллбэков выписок
        self.router.callback_query.register(self._cb_statement, F.data.in_({"stmt:month", "stmt:all"}))
