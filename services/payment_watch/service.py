from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging

from aiogram.types import Message

from services.admin_client import USDT_WALLET_SETTING_KEY
from services.payment_watch.address_parser import extract_tron_address_from_message
from services.payment_watch.message_builder import PaymentWatchMessageBuilder
from services.payment_watch.models import PaymentWatchNotification
from services.payment_watch.receipt_image import PaymentReceiptImageBuilder
from services.payment_watch.tronscan_gateway import TronscanGateway
from services.wallets.wallet_service import WalletService
from utils.info import get_chat_name


class PaymentWatchError(Exception):
    pass


log = logging.getLogger("payment_watch")


class PaymentWatchService:
    def __init__(
        self,
        *,
        repo,
        tronscan_gateway: TronscanGateway,
        wallet_service: WalletService | None = None,
        timeout_seconds: int = 15 * 60,
        test_amount: Decimal = Decimal("1"),
    ) -> None:
        self.repo = repo
        self.tronscan_gateway = tronscan_gateway
        self.wallet_service = wallet_service
        self.timeout_seconds = int(timeout_seconds)
        self.test_amount = Decimal(test_amount)
        self.builder = PaymentWatchMessageBuilder()
        self.receipt_builder = PaymentReceiptImageBuilder()

    async def start_watch_from_reply(
        self,
        *,
        message: Message,
        test_mode: bool,
        manager_note: str | None = None,
    ) -> tuple[int, str]:
        reply = message.reply_to_message
        if reply is None:
            raise PaymentWatchError("Команда должна быть ответом на сообщение с кошельком клиента.")

        address = extract_tron_address_from_message(reply)
        if not address:
            raise PaymentWatchError("В сообщении не найден TRON-кошелёк.")

        our_address_raw = await self.repo.get_setting(USDT_WALLET_SETTING_KEY)
        our_address = (our_address_raw or "").strip()
        if not our_address:
            raise PaymentWatchError("Наш USDT-кошелёк не задан. Сначала используйте /setwallet.")
        if our_address == address:
            raise PaymentWatchError("Кошелёк клиента совпадает с нашим USDT-кошельком.")

        existing = await self.repo.get_active_payment_watch_by_reply(
            chat_id=message.chat.id,
            reply_message_id=reply.message_id,
        )
        if existing:
            raise PaymentWatchError("Для этого сообщения уже запущено ожидание оплаты.")

        mode = "TEST_THEN_MAIN" if test_mode else "SINGLE"
        phase = "TEST" if test_mode else "MAIN"
        timeout_at = datetime.now(timezone.utc) + timedelta(seconds=self.timeout_seconds)
        watch_id = await self.repo.create_payment_watch(
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
            reply_message_id=reply.message_id,
            address=address,
            our_address=our_address,
            created_by_user_id=(message.from_user.id if message.from_user else None),
            mode=mode,
            phase=phase,
            status="WATCHING",
            timeout_at=timeout_at,
        )
        return watch_id, self.builder.build_started(
            address=address,
            test_mode=test_mode,
            manager_note=manager_note,
        )

    async def continue_watch(self, *, watch_id: int) -> str:
        watch = await self.repo.get_payment_watch(watch_id=watch_id)
        if not watch:
            raise PaymentWatchError("Наблюдение не найдено.")
        if str(watch.get("status")) != "TIMED_OUT":
            raise PaymentWatchError("Это наблюдение уже не ожидает подтверждения продолжения.")

        timeout_at = datetime.now(timezone.utc) + timedelta(seconds=self.timeout_seconds)
        updated = await self.repo.continue_payment_watch(watch_id=watch_id, timeout_at=timeout_at)
        if not updated:
            raise PaymentWatchError("Не удалось продлить ожидание.")
        return self.builder.build_continued()

    async def set_notice_message_id(self, *, watch_id: int, message_id: int) -> None:
        await self.repo.set_payment_watch_notice_message_id(
            watch_id=watch_id,
            notice_message_id=message_id,
        )

    async def stop_watch(self, *, watch_id: int) -> str:
        watch = await self.repo.get_payment_watch(watch_id=watch_id)
        if not watch:
            raise PaymentWatchError("Наблюдение не найдено.")
        updated = await self.repo.stop_payment_watch(watch_id=watch_id)
        if not updated:
            raise PaymentWatchError("Ожидание уже остановлено или завершено.")
        return self.builder.build_stopped()

    async def poll_once(self) -> list[PaymentWatchNotification]:
        notifications: list[PaymentWatchNotification] = []
        now = datetime.now(timezone.utc)
        watches = await self.repo.list_watching_payment_watches(limit=100)
        for watch in watches:
            watch_id = int(watch["id"])
            notice_message_id = int(watch["notice_message_id"]) if watch.get("notice_message_id") else None
            timeout_at = watch.get("timeout_at")
            if timeout_at and timeout_at <= now:
                changed = await self.repo.mark_payment_watch_timed_out(watch_id=watch_id)
                if changed:
                    notifications.append(
                        PaymentWatchNotification(
                            chat_id=int(watch["chat_id"]),
                            reply_message_id=int(watch["reply_message_id"]),
                            text=self.builder.build_timeout(),
                            watch_id=watch_id,
                            with_timeout_actions=True,
                            delete_message_id=notice_message_id,
                        )
                    )
                continue

            notifications.extend(await self._process_watch(watch))
            await self.repo.touch_payment_watch_checked_at(watch_id=watch_id, checked_at=now)
        return notifications

    async def _process_watch(self, watch: dict) -> list[PaymentWatchNotification]:
        watch_id = int(watch["id"])
        address = str(watch["address"])
        our_address = str(watch["our_address"])
        phase = str(watch["phase"]).upper()
        mode = str(watch["mode"]).upper()
        started_at = watch["started_at"]
        start_ms = int(started_at.timestamp() * 1000)
        seen_hashes = await self.repo.get_payment_watch_event_hashes(watch_id=watch_id)
        transfers = await self.tronscan_gateway.list_usdt_transfers(
            address=address,
            start_timestamp_ms=start_ms,
            limit=50,
        )

        notifications: list[PaymentWatchNotification] = []
        for transfer in transfers:
            if transfer.tx_hash in seen_hashes:
                continue
            if transfer.confirmations < 1:
                continue
            if not (
                (transfer.from_address == our_address and transfer.to_address == address)
                or (transfer.from_address == address and transfer.to_address == our_address)
            ):
                continue
            direction = "IN" if transfer.to_address == address else "OUT"

            if mode == "TEST_THEN_MAIN" and phase == "TEST":
                if transfer.amount != self.test_amount:
                    continue
                await self.repo.add_payment_watch_event(
                    watch_id=watch_id,
                    tx_hash=transfer.tx_hash,
                    event_type="TEST",
                    direction=direction,
                    amount=transfer.amount,
                    token_symbol=transfer.token_symbol,
                    confirmations=transfer.confirmations,
                    block_ts=transfer.block_ts,
                )
                await self.repo.set_payment_watch_phase(watch_id=watch_id, phase="MAIN")
                phase = "MAIN"
                seen_hashes.add(transfer.tx_hash)
                notifications.append(
                    PaymentWatchNotification(
                        chat_id=int(watch["chat_id"]),
                        reply_message_id=int(watch["reply_message_id"]),
                        text=self.builder.build_test_success(
                            amount=transfer.amount,
                            tx_hash=transfer.tx_hash,
                            from_address=transfer.from_address,
                            to_address=transfer.to_address,
                            block_number=transfer.block_number,
                        ),
                        delete_message_id=int(watch["notice_message_id"]) if watch.get("notice_message_id") else None,
                    )
                )
                continue

            if mode == "TEST_THEN_MAIN" and transfer.amount == self.test_amount:
                continue

            await self.repo.add_payment_watch_event(
                watch_id=watch_id,
                tx_hash=transfer.tx_hash,
                event_type="MAIN",
                direction=direction,
                amount=transfer.amount,
                token_symbol=transfer.token_symbol,
                confirmations=transfer.confirmations,
                block_ts=transfer.block_ts,
            )
            await self.repo.complete_payment_watch(watch_id=watch_id)
            photo_bytes: bytes | None = None
            try:
                photo_bytes = self.receipt_builder.build_main_success(
                    amount=transfer.amount,
                    recipient_address=transfer.to_address,
                    tx_hash=transfer.tx_hash,
                    block_ts=transfer.block_ts,
                )
            except Exception as exc:
                log.warning("Payment receipt image disabled: %s", exc)
            notifications.append(
                PaymentWatchNotification(
                    chat_id=int(watch["chat_id"]),
                    reply_message_id=int(watch["reply_message_id"]),
                    text=self.builder.build_main_success(
                        amount=transfer.amount,
                        tx_hash=transfer.tx_hash,
                    ),
                    delete_message_id=int(watch["notice_message_id"]) if watch.get("notice_message_id") else None,
                    photo_bytes=photo_bytes,
                    photo_filename=f"payment_receipt_{watch_id}.png" if photo_bytes else None,
                )
            )
            if self.wallet_service is not None and (
                transfer.to_address == our_address or transfer.from_address == our_address
            ):
                wallet_amount = transfer.amount if transfer.to_address == our_address else -transfer.amount
                wallet_result = await self.wallet_service.apply_external_currency_change(
                    chat_id=int(watch["chat_id"]),
                    chat_name=str(watch.get("chat_name") or watch["chat_id"]),
                    code="USDT",
                    amount=wallet_amount,
                    expr=f"{transfer.amount.normalize():f}",
                    source="payment_watch",
                    idempotency_key=f"payment_watch:{watch_id}:{transfer.tx_hash}:wallet",
                )
                notifications.append(
                    PaymentWatchNotification(
                        chat_id=int(watch["chat_id"]),
                        reply_message_id=None,
                        text=wallet_result.message_text,
                    )
                )
            break
        return notifications
