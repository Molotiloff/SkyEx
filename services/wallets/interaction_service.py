from __future__ import annotations

import logging

from aiogram.types import Message

from models.wallet import WalletError
from services.wallets.models import WalletCommandResult
from services.wallets.wallet_service import WalletService
from utils.info import get_chat_name
from utils.statements import statements_kb

log = logging.getLogger("wallets")


class WalletInteractionService:
    def __init__(self, *, wallet_service: WalletService) -> None:
        self.wallet_service = wallet_service

    async def build_wallet_response(self, message: Message) -> WalletCommandResult:
        text = await self.wallet_service.build_wallet_text(
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
        )
        return WalletCommandResult(ok=True, message_text=text, reply_markup=statements_kb())

    async def build_remove_currency_response(self, message: Message) -> WalletCommandResult:
        parts = (message.text or "").split()
        if len(parts) < 2:
            return WalletCommandResult(
                ok=False,
                message_text="Использование: /удали КОД\nПримеры: /удали USD, /удали дол, /удали юсдт",
            )

        return await self.wallet_service.build_remove_currency_confirmation(
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
            raw_code=parts[1],
        )

    async def build_add_currency_response(self, message: Message) -> WalletCommandResult:
        parts = (message.text or "").split()
        if len(parts) < 2:
            return WalletCommandResult(
                ok=False,
                message_text=(
                    "Использование: /добавь КОД [точность]\n"
                    "Примеры: /добавь USD 2, /добавь дол 2, /добавь юсдт 0, /добавь доллбел 2"
                ),
            )

        precision = 2
        if len(parts) >= 3:
            try:
                precision = int(parts[2])
            except ValueError:
                return WalletCommandResult(ok=False, message_text="Ошибка: точность должна быть целым числом 0..8")

        return await self.wallet_service.add_currency(
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
            raw_code=parts[1],
            precision=precision,
        )

    async def build_currency_change_response(self, message: Message) -> WalletCommandResult | None:
        try:
            parsed = await self.wallet_service.parse_currency_change(message)
            if not parsed:
                return None

            return await self.wallet_service.apply_currency_change(
                message=message,
                parsed=parsed,
            )
        except ValueError as e:
            return WalletCommandResult(ok=False, message_text=str(e))
        except WalletError as we:
            log.exception("WalletError in _on_currency_change chat_id=%s msg_id=%s",
                          message.chat.id, message.message_id)
            return WalletCommandResult(ok=False, message_text=f"Ошибка: {we}")
        except Exception as e:
            log.exception("Exception in _on_currency_change chat_id=%s msg_id=%s",
                          message.chat.id, message.message_id)
            return WalletCommandResult(ok=False, message_text=f"Не удалось обработать операцию: {e}")

    def parse_remove_currency_callback(self, data: str | None) -> tuple[str, str]:
        try:
            _, code_raw, answer = (data or "").split(":")
        except Exception as e:
            raise ValueError("Некорректные данные") from e
        return code_raw, answer

    async def build_remove_currency_callback_response(
        self,
        *,
        message: Message,
        code_raw: str,
        answer: str,
    ) -> tuple[str, str, bool]:
        if answer == "no":
            code = self.wallet_service.normalize_code_alias(code_raw)
            return f"Удаление {code} отменено.", "Отмена", False

        result = await self.wallet_service.remove_currency_confirmed(
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
            code_raw=code_raw,
        )
        return result.message_text, "Удалено" if result.ok else "Отклонено", not result.ok

    def parse_undo_callback(self, data: str | None) -> tuple[str, str, str] | None:
        try:
            kind, code_raw, sign, amt_str = (data or "").split(":")
        except Exception as e:
            raise ValueError("Некорректные данные") from e

        if kind != "undo":
            return None
        return code_raw, sign, amt_str

    async def build_undo_response(
        self,
        *,
        message: Message,
        code_raw: str,
        sign: str,
        amt_str: str,
    ) -> WalletCommandResult:
        return await self.wallet_service.undo_operation(
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
            message_id=message.message_id,
            code_raw=code_raw,
            sign=sign,
            amt_str=amt_str,
        )
