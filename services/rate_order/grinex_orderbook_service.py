from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from db_asyncpg.repo import Repo

log = logging.getLogger("grinex_orderbook")


class GrinexOrderbookService:
    LIVE_MESSAGE_KEY = "grinex_live"

    def __init__(self, *, ws_service, repo: Repo) -> None:
        self.ws_service = ws_service
        self.repo = repo

        self._live_chat_id: int | None = None
        self._live_message_id: int | None = None

    @staticmethod
    def _fmt_num(v: Decimal) -> str:
        return f"{v:,.2f}"

    # ---------- LIVE MESSAGE CONTROL ----------

    async def set_live_message(self, *, chat_id: int, message_id: int) -> None:
        self._live_chat_id = int(chat_id)
        self._live_message_id = int(message_id)

        await self.repo.upsert_live_message(
            chat_id=self._live_chat_id,
            message_key=self.LIVE_MESSAGE_KEY,
            message_id=self._live_message_id,
        )

    async def clear_live_message(self) -> None:
        if self._live_chat_id:
            await self.repo.delete_live_message(
                chat_id=self._live_chat_id,
                message_key=self.LIVE_MESSAGE_KEY,
            )

        self._live_chat_id = None
        self._live_message_id = None

    async def restore_live_message(self, *, admin_chat_id: int | None) -> None:
        if not admin_chat_id:
            return

        row = await self.repo.get_live_message(
            chat_id=int(admin_chat_id),
            message_key=self.LIVE_MESSAGE_KEY,
        )

        if not row:
            return

        self._live_chat_id = int(row["chat_id"])
        self._live_message_id = int(row["message_id"])

        log.info(
            "Restored live message: chat_id=%s message_id=%s",
            self._live_chat_id,
            self._live_message_id,
        )

    # ---------- BUILD TEXT ----------

    def build_asks_depth_text(
        self,
        *,
        min_total_volume: Decimal = Decimal("500000"),
        min_order_volume: Decimal = Decimal("1000"),
    ) -> str:
        asks = self.ws_service.get_asks()
        if not asks:
            return "〽️ Глубина стакана продаж для USDT/A7A5\n\nСтакан Grinex пока недоступен."

        rows: list[tuple[Decimal, Decimal]] = []
        total_volume = Decimal("0")

        for item in asks:
            try:
                price = Decimal(str(item["price"]))
                volume = Decimal(str(item["volume"]))
            except Exception:
                continue

            if volume < min_order_volume:
                continue

            rows.append((price, volume))
            total_volume += volume

            if total_volume >= min_total_volume:
                break

        if not rows:
            return "〽️ Глубина стакана продаж для USDT/A7A5\n\nНет значимых ордеров (все < 1000)."

        price_width = max(len("Цена"), *(len(self._fmt_num(p)) for p, _ in rows))
        volume_width = max(len("Объём"), *(len(self._fmt_num(v)) for _, v in rows))

        header = f"{'Цена'.ljust(price_width)} | {'Объём'.rjust(volume_width)}"
        sep = "-" * (price_width + 3 + volume_width)

        lines = [
            "〽️ Глубина стакана продаж для USDT/A7A5",
            header,
            sep,
        ]

        for price, volume in rows:
            lines.append(
                f"{self._fmt_num(price).rjust(price_width)} | {self._fmt_num(volume).rjust(volume_width)}"
            )

        lines += [
            sep,
            f"Всего объём: {self._fmt_num(total_volume)}",
            f"Количество ордеров: {len(rows)}",
        ]

        return "\n".join(lines)

    def build_first_bid_text(self) -> str:
        bids = self.ws_service.get_bids()
        if not bids:
            return "〽️ Первый ордер на покупку для USDT/A7A5\n\nСтакан Grinex пока недоступен."

        try:
            first = bids[0]
            price = Decimal(str(first["price"]))
            volume = Decimal(str(first["volume"]))
        except Exception:
            return "〽️ Первый ордер на покупку для USDT/A7A5\n\nСтакан Grinex пока недоступен."

        return (
            "〽️ Первый ордер на покупку для USDT/A7A5\n\n"
            f"Цена: {self._fmt_num(price)}\n"
            f"Объём: {self._fmt_num(volume)}"
        )

    def build_live_text(
            self,
            *,
            min_total_volume: Decimal = Decimal("500000"),
            min_order_volume: Decimal = Decimal("1000"),
    ) -> str:
        return (
            f"{self.build_asks_depth_text(
                min_total_volume=min_total_volume,
                min_order_volume=min_order_volume,
            )}\n\n"
            f"{self.build_first_bid_text()}"
        )

    # ---------- UPDATE MESSAGE ----------

    async def refresh_live_message(self, *, bot: Bot) -> None:
        if not self._live_chat_id or not self._live_message_id:
            return

        text = self.build_live_text()

        try:
            await bot.edit_message_text(
                chat_id=self._live_chat_id,
                message_id=self._live_message_id,
                text=text,
            )

        except TelegramBadRequest as e:
            err = str(e).lower()

            if "message is not modified" in err:
                return

            if "message to edit not found" in err or "chat not found" in err:
                await self.clear_live_message()
                return

            log.warning(
                "Failed to refresh live message chat_id=%s message_id=%s: %r",
                self._live_chat_id,
                self._live_message_id,
                e,
            )

        except Exception as e:
            log.warning(
                "Unexpected error updating live message chat_id=%s message_id=%s: %r",
                self._live_chat_id,
                self._live_message_id,
                e,
            )