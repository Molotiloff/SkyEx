# utils/city_cash_transfer.py
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from aiogram import Bot
from aiogram.types import Message

from db_asyncpg.repo import Repo
from utils.formatting import format_amount_core, format_amount_with_sign
from utils.info import get_chat_name
from utils.tg_migrate import send_with_migrate_retry


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
    # 1) найти клиента
    found = await repo.find_client_by_name_exact(client_name_exact)
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

    # 2) проверить, что у клиента есть счёт и взять точность
    target_accounts = await repo.snapshot_wallet(target_client_id)
    target_acc = next((r for r in target_accounts if str(r["currency_code"]).upper() == code), None)
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
    if delta_abs == 0:
        return CityTransferResult(
            ok=False,
            error=f"⚠️ Сумма слишком мала для точности клиента ({target_prec}).",
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
        )

    # 3) применить операцию в кошельке клиента (идемпотентность)
    idem2 = f"city_transfer:{src_message.chat.id}:{src_message.message_id}:to:{target_chat_id}"

    city_tag = get_chat_name(src_message)
    comment = amount_expr
    if extra_comment:
        comment = f"{amount_expr} | {extra_comment}"
    comment = f"{comment} | касса: {city_tag}"

    if amount_signed > 0:
        await repo.deposit(
            client_id=target_client_id,
            currency_code=code,
            amount=delta_abs,
            comment=comment,
            source="city_transfer",
            idempotency_key=idem2,
        )
        pretty_delta = format_amount_with_sign(delta_abs, target_prec, sign="+")
        sign_for_caption = ""   # "/руб 10000 ..."
    else:
        await repo.withdraw(
            client_id=target_client_id,
            currency_code=code,
            amount=delta_abs,
            comment=comment,
            source="city_transfer",
            idempotency_key=idem2,
        )
        pretty_delta = format_amount_with_sign(delta_abs, target_prec, sign="-")
        sign_for_caption = "-"  # если хочешь "/руб -10000 ..."

    # 4) отправить фото/квитанцию в клиентский чат (с migrate-retry)
    photo_file_id = _pick_photo_file_id(src_message)

    caption = f"/{code.lower()} {sign_for_caption}{amount_expr}".strip()
    if extra_comment:
        caption += f" {extra_comment}"

    if photo_file_id:
        _, new_chat_id = await send_with_migrate_retry(
            repo=repo,
            client_id=target_client_id,
            chat_id=target_chat_id,
            send_call=lambda cid: bot.send_photo(chat_id=cid, photo=photo_file_id, caption=caption),
        )
    else:
        _, new_chat_id = await send_with_migrate_retry(
            repo=repo,
            client_id=target_client_id,
            chat_id=target_chat_id,
            send_call=lambda cid: bot.send_message(chat_id=cid, text=caption),
        )

    # если мигрировали — дальше работаем с новым id
    if new_chat_id is not None:
        target_chat_id = int(new_chat_id)

    # 5) посчитать и отправить баланс клиента (тоже с migrate-retry)
    target_accounts2 = await repo.snapshot_wallet(target_client_id)
    target_acc2 = next((r for r in target_accounts2 if str(r["currency_code"]).upper() == code), None)
    target_bal = Decimal(str(target_acc2["balance"])) if target_acc2 else Decimal("0")
    target_prec2 = int(target_acc2["precision"]) if target_acc2 and target_acc2.get("precision") is not None else target_prec
    pretty_bal = format_amount_core(target_bal, target_prec2)

    await send_with_migrate_retry(
        repo=repo,
        client_id=target_client_id,
        chat_id=target_chat_id,
        send_call=lambda cid: bot.send_message(
            chat_id=cid,
            text=f"Запомнил. {pretty_delta}\nБаланс: {pretty_bal} {code.lower()}",
        ),
    )

    return CityTransferResult(
        ok=True,
        target_chat_id=target_chat_id,
        target_client_id=target_client_id,
        pretty_delta=pretty_delta,
        pretty_balance=pretty_bal,
    )