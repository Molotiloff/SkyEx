# utils/city_cash_transfer.py
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Callable, Awaitable, Any

from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

from db_asyncpg.repo import Repo
from utils.formatting import format_amount_core, format_amount_with_sign
from utils.info import get_chat_name

log = logging.getLogger("city_cash_transfer")

_MIGRATE_RE = re.compile(r"migrated to a supergroup with id\s+(-100\d+)\s+from\s+(-?\d+)", re.I)


@dataclass(frozen=True, slots=True)
class CityTransferResult:
    ok: bool
    error: str | None = None
    target_chat_id: int | None = None
    target_client_id: int | None = None
    pretty_delta: str | None = None
    pretty_balance: str | None = None


def _pick_photo_file_id(message: Message) -> Optional[str]:
    if not message.photo:
        return None
    return message.photo[-1].file_id


def _extract_migrate_to_chat_id(err: BaseException) -> int | None:
    # aiogram/TelegramBadRequest обычно содержит migrate_to_chat_id в args/строке
    s = str(err)
    m = _MIGRATE_RE.search(s)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    # иногда TelegramBadRequest имеет attribute .message / .parameters — но не всегда стабильно
    return None


async def _safe_send_with_migration(
    *,
    bot: Bot,
    repo: Repo,
    target_client_id: int,
    target_chat_id: int,
    send_coro_factory: Callable[[int], Awaitable[Any]],
) -> int:
    """
    Пытается отправить в target_chat_id.
    Если чат мигрирован — обновляет clients.chat_id и повторяет отправку.
    Возвращает актуальный chat_id.
    """
    try:
        await send_coro_factory(target_chat_id)
        return target_chat_id
    except TelegramBadRequest as e:
        migrate_to = _extract_migrate_to_chat_id(e)
        log.exception(
            "TelegramBadRequest while sending. target_chat_id=%s migrate_to=%s err=%s",
            target_chat_id, migrate_to, str(e),
        )
        if not migrate_to:
            raise

        # обновляем chat_id в БД
        log.warning("Updating client chat_id in DB: client_id=%s %s -> %s", target_client_id, target_chat_id, migrate_to)
        await repo.update_client_chat_id(client_id=target_client_id, new_chat_id=migrate_to)

        # повторяем отправку уже в новый чат
        await send_coro_factory(migrate_to)
        return migrate_to


async def city_cash_transfer_to_client(
    *,
    repo: Repo,
    bot: Bot,
    src_message: Message,
    currency_code: str,
    amount_signed: Decimal,
    amount_expr: str,
    client_name_exact: str,
    extra_comment: str = "",
) -> CityTransferResult:
    log.info(
        "START transfer src_chat_id=%s src_msg_id=%s code=%s amount_signed=%s amount_expr=%r client_name=%r",
        getattr(src_message.chat, "id", None),
        getattr(src_message, "message_id", None),
        currency_code,
        str(amount_signed),
        amount_expr,
        client_name_exact,
    )

    found = await repo.find_client_by_name_exact(client_name_exact)
    log.info("find_client_by_name_exact(%r) -> %s", client_name_exact, found)

    if not found:
        return CityTransferResult(
            ok=False,
            error=(
                "⚠️ Не нашёл клиента по имени.\n"
                "Проверьте, что имя вставлено ТОЧНО как в строке 'Клиент:' (включая пробелы и символы)."
            ),
        )

    target_chat_id = int(found["chat_id"])
    target_client_id = int(found["id"])
    code = (currency_code or "").strip().upper()

    # кошелёк клиента
    target_accounts = await repo.snapshot_wallet(target_client_id)
    target_acc = next((r for r in target_accounts if str(r["currency_code"]).upper() == code), None)
    log.info("target_wallet client_id=%s code=%s acc_found=%s", target_client_id, code, bool(target_acc))

    if not target_acc:
        return CityTransferResult(
            ok=False,
            error=(
                f"⚠️ У клиента нет счёта {code}.\n"
                f"Нужно добавить в клиентском чате: /добавь {code} [точность]"
            ),
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
        )

    target_prec = int(target_acc["precision"]) if target_acc.get("precision") is not None else 2
    q = Decimal(10) ** -target_prec
    delta_abs = amount_signed.copy_abs().quantize(q, rounding=ROUND_HALF_UP)
    log.info("quantize for client: prec=%s delta_abs=%s", target_prec, str(delta_abs))

    if delta_abs == 0:
        return CityTransferResult(
            ok=False,
            error=f"⚠️ Сумма слишком мала для точности клиента ({target_prec}).",
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
        )

    idem2 = f"city_transfer:{src_message.chat.id}:{src_message.message_id}:to:{target_chat_id}"
    city_tag = get_chat_name(src_message)

    comment = amount_expr
    if extra_comment:
        comment = f"{amount_expr} | {extra_comment}"
    comment = f"{comment} | касса: {city_tag}"

    # применяем операцию клиенту
    try:
        if amount_signed > 0:
            log.info("repo.deposit client_id=%s code=%s amount=%s idem=%s", target_client_id, code, str(delta_abs), idem2)
            await repo.deposit(
                client_id=target_client_id,
                currency_code=code,
                amount=delta_abs,
                comment=comment,
                source="city_transfer",
                idempotency_key=idem2,
            )
            pretty_delta = format_amount_with_sign(delta_abs, target_prec, sign="+")
            sign_for_caption = ""
        else:
            log.info("repo.withdraw client_id=%s code=%s amount=%s idem=%s", target_client_id, code, str(delta_abs), idem2)
            await repo.withdraw(
                client_id=target_client_id,
                currency_code=code,
                amount=delta_abs,
                comment=comment,
                source="city_transfer",
                idempotency_key=idem2,
            )
            pretty_delta = format_amount_with_sign(delta_abs, target_prec, sign="-")
            sign_for_caption = "-"
    except Exception as e:
        log.exception("FAILED apply wallet delta for client_id=%s: %s", target_client_id, str(e))
        raise

    photo_file_id = _pick_photo_file_id(src_message)
    caption = f"/{code.lower()} {sign_for_caption}{amount_expr}".strip()
    if extra_comment:
        caption += f" {extra_comment}"

    # 1) отправка фото/квитанции с авто-миграцией
    try:
        if photo_file_id:
            log.info("send_photo to chat_id=%s file_id=%s caption=%r", target_chat_id, photo_file_id, caption)

            async def _send(chat_id: int):
                return await bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=caption)

            target_chat_id = await _safe_send_with_migration(
                bot=bot, repo=repo,
                target_client_id=target_client_id,
                target_chat_id=target_chat_id,
                send_coro_factory=_send,
            )
        else:
            log.info("send_message to chat_id=%s text=%r", target_chat_id, caption)

            async def _send(chat_id: int):
                return await bot.send_message(chat_id=chat_id, text=caption)

            target_chat_id = await _safe_send_with_migration(
                bot=bot, repo=repo,
                target_client_id=target_client_id,
                target_chat_id=target_chat_id,
                send_coro_factory=_send,
            )
    except Exception as e:
        log.exception("FAILED sending receipt to client chat. client_id=%s chat_id=%s", target_client_id, target_chat_id)
        raise

    # 2) баланс клиента (тоже через safe-send, чтобы если миграция — не упало тут)
    target_accounts2 = await repo.snapshot_wallet(target_client_id)
    target_acc2 = next((r for r in target_accounts2 if str(r["currency_code"]).upper() == code), None)
    target_bal = Decimal(str(target_acc2["balance"])) if target_acc2 else Decimal("0")
    target_prec2 = int(target_acc2["precision"]) if target_acc2 and target_acc2.get("precision") is not None else target_prec
    pretty_bal = format_amount_core(target_bal, target_prec2)

    bal_text = f"Запомнил. {pretty_delta}\nБаланс: {pretty_bal} {code.lower()}"
    log.info("send balance msg to chat_id=%s text=%r", target_chat_id, bal_text)

    async def _send_bal(chat_id: int):
        return await bot.send_message(chat_id=chat_id, text=bal_text)

    target_chat_id = await _safe_send_with_migration(
        bot=bot, repo=repo,
        target_client_id=target_client_id,
        target_chat_id=target_chat_id,
        send_coro_factory=_send_bal,
    )

    log.info("DONE transfer ok client_id=%s chat_id=%s", target_client_id, target_chat_id)

    return CityTransferResult(
        ok=True,
        target_chat_id=target_chat_id,
        target_client_id=target_client_id,
        pretty_delta=pretty_delta,
        pretty_balance=pretty_bal,
    )