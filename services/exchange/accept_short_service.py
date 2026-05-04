from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from aiogram.types import CallbackQuery, Message

from db_asyncpg.ports import ExchangeWorkflowRepositoryPort
from services.act_counter import ActCounterService
from utils.calc import CalcError, evaluate
from utils.exchange_base import AbstractExchangeHandler


@dataclass(slots=True, frozen=True)
class ParsedAcceptShortCommand:
    recv_code: str
    recv_amount_expr: str
    pay_code: str
    pay_amount_expr: str
    user_note: str | None
    recv_amount: Decimal
    pay_amount: Decimal
    recv_prec: int
    pay_prec: int
    rate_str: str


class AcceptShortService(AbstractExchangeHandler):
    RECV_MAP = {
        "пд": "USD",
        "пе": "EUR",
        "пт": "USDT",
        "пр": "RUB",
        "пб": "USDW",
        "прмск": "РУБМСК",
        "прспб": "РУБСПБ",
        "прпер": "РУБПЕР",
        "пп": "EUR500",
    }
    PAY_MAP = {
        "од": "USD",
        "ое": "EUR",
        "от": "USDT",
        "ор": "RUB",
        "об": "USDW",
        "ормск": "РУБМСК",
        "орспб": "РУБСПБ",
        "орпер": "РУБПЕР",
        "оп": "EUR500",
    }
    RUB_CODES = {"RUB", "РУБМСК", "РУБСПБ", "РУБПЕР"}
    _COMMAND_RE = re.compile(
        r"^/(пд|пе|пт|пр|пб|прмск|прспб|прпер|пп)(?:@\w+)?\s+(.+?)\s+"
        r"(од|ое|от|ор|об|ормск|орспб|орпер|оп)\s+(\S+)(?:\s+(.+))?$",
        flags=re.IGNORECASE | re.UNICODE,
    )

    def __init__(
        self,
        repo: ExchangeWorkflowRepositoryPort,
        request_chat_id: int | None = None,
        act_counter_service: ActCounterService | None = None,
    ) -> None:
        super().__init__(
            repo,
            request_chat_id=request_chat_id,
            act_counter_service=act_counter_service,
        )

    def _is_request_chat_origin(self, chat_id: int) -> bool:
        return bool(self.request_chat_id and int(chat_id) == int(self.request_chat_id))

    @staticmethod
    def _fmt_rate(value: Decimal) -> str:
        normalized = f"{value.normalize():f}"
        return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized

    @classmethod
    def help_text(cls) -> str:
        return (
            "Формат:\n"
            "  /пд|/пе|/пт|/пр|/пб <сумма/expr> <од|ое|от|ор|об> <сумма/expr> [комментарий]\n\n"
            "Примеры:\n"
            "• /пд 1000 ое 1000/0.92 Клиент Петров\n"
            "• /пе (2500+500) ор 300000 «наличные»\n"
            "• /пт 700 од 700*1.08 срочно\n"
            "• /пр 100000 от 100000/94 договор №42"
        )

    async def parse_command(self, message: Message) -> ParsedAcceptShortCommand | None:
        raw = message.text or ""
        match = self._COMMAND_RE.match(raw)
        if not match:
            return None

        recv_key = match.group(1).lower()
        recv_amount_expr = match.group(2).strip()
        pay_key = match.group(3).lower()
        pay_amount_expr = match.group(4).strip()
        user_note = (match.group(5) or "").strip() or None

        recv_code = self.RECV_MAP.get(recv_key)
        pay_code = self.PAY_MAP.get(pay_key)
        if not recv_code or not pay_code:
            raise ValueError(
                "Не распознал валюты. Используйте: /пд /пе /пт /пр /пб /прмск /прспб и "
                "од/ое/от/ор/об/ормск/орспб."
            )

        try:
            recv_raw = evaluate(recv_amount_expr)
            pay_raw = evaluate(pay_amount_expr)
            if recv_raw <= 0 or pay_raw <= 0:
                raise ValueError("Суммы должны быть > 0")
            _ = recv_raw / pay_raw
        except ValueError:
            raise
        except (CalcError, InvalidOperation, ZeroDivisionError) as exc:
            raise ValueError(f"Ошибка в выражениях: {exc}") from exc

        client_id = await self.repo.ensure_client(
            chat_id=message.chat.id,
            name=(message.chat.full_name or ""),
        )
        accounts = await self.repo.snapshot_wallet(client_id)

        acc_recv = next((row for row in accounts if str(row["currency_code"]).upper() == recv_code), None)
        acc_pay = next((row for row in accounts if str(row["currency_code"]).upper() == pay_code), None)
        if not acc_recv or not acc_pay:
            missing = recv_code if not acc_recv else pay_code
            raise ValueError(f"Счёт {missing} не найден. Добавьте валюту: /добавь {missing} [точность]")

        recv_prec = int(acc_recv["precision"])
        pay_prec = int(acc_pay["precision"])

        q_recv = Decimal(10) ** -recv_prec
        q_pay = Decimal(10) ** -pay_prec
        recv_amount = recv_raw.quantize(q_recv, rounding=ROUND_HALF_UP)
        pay_amount = pay_raw.quantize(q_pay, rounding=ROUND_HALF_UP)
        if recv_amount == 0 or pay_amount == 0:
            raise ValueError("Сумма слишком мала для точности выбранных валют.")

        try:
            service_cls = self.__class__
            if recv_code in service_cls.RUB_CODES or pay_code in service_cls.RUB_CODES:
                if recv_code in service_cls.RUB_CODES:
                    rub_raw = recv_raw
                    other_raw = pay_raw
                else:
                    rub_raw = pay_raw
                    other_raw = recv_raw
                rate = rub_raw / other_raw
            else:
                rate = pay_raw / recv_raw
            if not rate.is_finite() or rate <= 0:
                raise ValueError("Курс невалидный.")
            rate_str = service_cls._fmt_rate(rate.quantize(Decimal("1e-8")))
        except ValueError:
            raise
        except (InvalidOperation, ZeroDivisionError) as exc:
            raise ValueError("Ошибка расчёта курса.") from exc

        return ParsedAcceptShortCommand(
            recv_code=recv_code,
            recv_amount_expr=recv_amount_expr,
            pay_code=pay_code,
            pay_amount_expr=pay_amount_expr,
            user_note=user_note,
            recv_amount=recv_amount,
            pay_amount=pay_amount,
            recv_prec=recv_prec,
            pay_prec=pay_prec,
            rate_str=rate_str,
        )

    async def handle_command(self, message: Message) -> None:
        parsed = await self.parse_command(message)
        if not parsed:
            await message.answer(self.help_text())
            return

        is_request_chat_origin = self._is_request_chat_origin(message.chat.id)
        recv_is_deposit = is_request_chat_origin
        pay_is_withdraw = is_request_chat_origin

        handled = await self.try_edit_request(
            message=message,
            recv_code=parsed.recv_code,
            pay_code=parsed.pay_code,
            recv_amount=parsed.recv_amount,
            pay_amount=parsed.pay_amount,
            recv_prec=parsed.recv_prec,
            pay_prec=parsed.pay_prec,
            rate_str=parsed.rate_str,
            user_note=parsed.user_note,
            recv_is_deposit=recv_is_deposit,
            pay_is_withdraw=pay_is_withdraw,
        )
        if handled:
            return

        await self.process(
            message,
            recv_code=parsed.recv_code,
            recv_amount_expr=parsed.recv_amount_expr,
            pay_code=parsed.pay_code,
            pay_amount_expr=parsed.pay_amount_expr,
            recv_is_deposit=recv_is_deposit,
            pay_is_withdraw=pay_is_withdraw,
            note=parsed.user_note,
        )

    async def handle_cancel(self, cq: CallbackQuery) -> None:
        chat_id = cq.message.chat.id if cq.message and cq.message.chat else 0
        is_request_chat_origin = self._is_request_chat_origin(chat_id)
        await self.handle_cancel_callback(
            cq,
            recv_is_deposit=is_request_chat_origin,
            pay_is_withdraw=is_request_chat_origin,
        )
