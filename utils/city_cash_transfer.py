# utils/city_cash_transfer.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Awaitable, Callable

from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramMigrateToChat, TelegramBadRequest

from db_asyncpg.repo import Repo
from utils.formatting import format_amount_core, format_amount_with_sign
from utils.info import get_chat_name

log = logging.getLogger("city_cash_transfer")


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


async def _safe_send_with_migration(
    *,
    repo: Repo,
    bot: Bot,
    target_chat_id: int,
    target_client_id: int,
    send_coro_factory: Callable[[int], Awaitable[object]],
) -> int:
    """
    Пытается отправить в target_chat_id.
    Если чат мигрировал group->supergroup, обновляет clients.chat_id и ретраит.
    Возвращает актуальный chat_id (возможно новый).
    """
    try:
        await send_coro_factory(target_chat_id)
        return target_chat_id
    except TelegramMigrateToChat as e:
        new_chat_id = int(getattr(e, "migrate_to_chat_id", 0) or 0)
        if not new_chat_id:
            raise

        log.warning(
            "CHAT MIGRATION detected: old_chat_id=%s -> new_chat_id=%s (client_id=%s)",
            target_chat_id, new_chat_id, target_client_id,
        )

        # 1) обновляем в БД
        try:
            await repo.update_client_chat_id(client_id=target_client_id, new_chat_id=new_chat_id)
            log.info("clients.chat_id updated: client_id=%s new_chat_id=%s", target_client_id, new_chat_id)
        except Exception:
            log.exception("FAILED updating clients.chat_id client_id=%s new_chat_id=%s", target_client_id, new_chat_id)
            # даже если БД не обновилась — пробуем отправить по новому chat_id

        # 2) ретраим отправку в новый чат
        await send_coro_factory(new_chat_id)
        return new_chat_id


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
        src_message.chat.id, src_message.message_id,
        (currency_code or "").strip().upper(),
        str(amount_signed),
        amount_expr,
        client_name_exact,
    )

    # 1) найти клиента
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

    # 2) проверить счёт клиента и точность
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

    # 3) применить операцию в кошельке клиента
    idem2 = f"city_transfer:{src_message.chat.id}:{src_message.message_id}:to:{target_chat_id}"

    city_tag = get_chat_name(src_message)
    comment = amount_expr
    if extra_comment:
        comment = f"{amount_expr} | {extra_comment}"
    comment = f"{comment} | касса: {city_tag}"

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
    except Exception:
        log.exception("FAILED repo operation for client_id=%s code=%s", target_client_id, code)
        return CityTransferResult(
            ok=False,
            error="⚠️ Не удалось применить операцию в кошельке клиента.",
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
        )

    # 4) отправить фото/квитанцию
    photo_file_id = _pick_photo_file_id(src_message)
    caption = f"/{code.lower()} {sign_for_caption}{amount_expr}".strip()
    if extra_comment:
        caption += f" {extra_comment}"

    async def _send_receipt(chat_id: int):
        if photo_file_id:
            log.info("send_photo to chat_id=%s caption=%r", chat_id, caption)
            return await bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=caption)
        else:
            log.info("send_message to chat_id=%s text=%r", chat_id, caption)
            return await bot.send_message(chat_id=chat_id, text=caption)

    try:
        target_chat_id = await _safe_send_with_migration(
            repo=repo,
            bot=bot,
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
            send_coro_factory=_send_receipt,
        )
    except Exception:
        log.exception("FAILED sending receipt to client chat. client_id=%s chat_id=%s", target_client_id, target_chat_id)
        return CityTransferResult(
            ok=False,
            error="⚠️ Не удалось отправить квитанцию/фото в чат клиента (проверьте доступ бота).",
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
        )

    # 5) отправить баланс клиента после операции (тоже через safe-migration)
    try:
        target_accounts2 = await repo.snapshot_wallet(target_client_id)
        target_acc2 = next((r for r in target_accounts2 if str(r["currency_code"]).upper() == code), None)
        target_bal = Decimal(str(target_acc2["balance"])) if target_acc2 else Decimal("0")
        target_prec2 = int(target_acc2["precision"]) if target_acc2 and target_acc2.get("precision") is not None else target_prec
        pretty_bal = format_amount_core(target_bal, target_prec2)

        async def _send_balance(chat_id: int):
            text = f"Запомнил. {pretty_delta}\nБаланс: {pretty_bal} {code.lower()}"
            log.info("send_balance to chat_id=%s text=%r", chat_id, text)
            return await bot.send_message(chat_id=chat_id, text=text)

        target_chat_id = await _safe_send_with_migration(
            repo=repo,
            bot=bot,
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
            send_coro_factory=_send_balance,
        )

    except Exception:
        log.exception("FAILED sending balance to client chat. client_id=%s chat_id=%s", target_client_id, target_chat_id)
        # баланс не критичен — считаем успехом, но вернём предупреждение
        return CityTransferResult(
            ok=True,
            error="⚠️ Операцию продублировал, но не смог отправить баланс (проверьте права бота).",
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
            pretty_delta=pretty_delta,
            pretty_balance=None,
        )

    log.info("DONE transfer client_id=%s chat_id=%s delta=%s bal=%s", target_client_id, target_chat_id, pretty_delta, pretty_bal)

    return CityTransferResult(
        ok=True,
        target_chat_id=target_chat_id,
        target_client_id=target_client_id,
        pretty_delta=pretty_delta,
        pretty_balance=pretty_bal,
    )