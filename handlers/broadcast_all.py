from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
import logging

from db_asyncpg.repo import Repo
from utils.auth import require_manager_or_admin_message
from utils.broadcast_texts import (
    BROADCAST_ALL_TEXT,
    BROADCAST_IMAGE_PATH,
)

logger = logging.getLogger(__name__)


class BroadcastAllHandler:
    def __init__(
        self,
        repo: Repo,
        admin_chat_ids: set[int] | None = None,
        admin_user_ids: set[int] | None = None,
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.router = Router()
        self._register()

    async def _cmd_all(self, message: Message) -> None:
        # доступ только менеджерам / админам
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        clients = await self.repo.list_clients()
        if not clients:
            await message.answer("Нет активных клиентов для рассылки.")
            return

        photo = FSInputFile(BROADCAST_IMAGE_PATH)

        sent = 0
        blocked = 0
        not_found = 0
        skipped = 0
        other_errors = 0

        for c in clients:
            chat_id = c.get("chat_id")
            if not chat_id:
                skipped += 1
                continue

            try:
                await message.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=BROADCAST_ALL_TEXT,
                    parse_mode="HTML",
                )
                sent += 1

            except TelegramForbiddenError:
                # пользователь заблокировал бота
                blocked += 1

            except TelegramBadRequest as e:
                text = str(e).lower()
                if "chat not found" in text or "bot was blocked" in text:
                    not_found += 1
                else:
                    other_errors += 1
                    logger.exception("TelegramBadRequest for chat_id=%s", chat_id)

            except Exception:
                other_errors += 1
                logger.exception("Unexpected error for chat_id=%s", chat_id)

        await message.answer(
            "📣 <b>Рассылка завершена</b>\n\n"
            f"✅ Отправлено: <b>{sent}</b>\n",
            parse_mode="HTML",
        )

    def _register(self) -> None:
        self.router.message.register(self._cmd_all, Command("всем"))