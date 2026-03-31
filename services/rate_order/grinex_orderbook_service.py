from __future__ import annotations

from decimal import Decimal


class GrinexOrderbookService:
    def __init__(self, *, ws_service) -> None:
        self.ws_service = ws_service

    @staticmethod
    def _fmt_num(v: Decimal) -> str:
        return f"{v:,.2f}"

    def build_asks_depth_text(
            self,
            *,
            min_total_volume: Decimal = Decimal("500000"),
            min_order_volume: Decimal = Decimal("1000"),
    ) -> str:
        asks = self.ws_service.get_asks()
        if not asks:
            return "Стакан Grinex пока недоступен."

        rows: list[tuple[Decimal, Decimal]] = []
        total_volume = Decimal("0")

        for item in asks:
            try:
                price = Decimal(str(item["price"]))
                volume = Decimal(str(item["volume"]))
            except Exception:
                continue

            # ❗ фильтр мелких ордеров
            if volume < min_order_volume:
                continue

            rows.append((price, volume))
            total_volume += volume

            if total_volume > min_total_volume:
                break

        if not rows:
            return "Нет значимых ордеров (все < 1000)."

        def fmt(v: Decimal) -> str:
            return f"{v:,.2f}"

        price_width = max(len(fmt(p)) for p, _ in rows)
        volume_width = max(len(fmt(v)) for _, v in rows)

        header = f"{'Цена'.ljust(price_width)} | {'Объём'.rjust(volume_width)}"
        sep = "-" * (price_width + 3 + volume_width)

        lines = [
            "〽️ <b>Глубина стакана продаж для USDT/A7A5</b>",
            header,
            sep,
        ]

        for price, volume in rows:
            lines.append(
                f"{fmt(price).rjust(price_width)} | {fmt(volume).rjust(volume_width)}"
            )

        lines += [
            sep,
            f"Всего объём: {fmt(total_volume)}",
            f"Количество ордеров: {len(rows)}",
        ]

        return "\n".join(lines)

    def build_first_bid_text(self) -> str:
        bids = self.ws_service.get_bids()
        if not bids:
            return "Стакан Grinex пока недоступен."

        try:
            first = bids[0]
            price = Decimal(str(first["price"]))
            volume = Decimal(str(first["volume"]))
        except Exception:
            return "Стакан Grinex пока недоступен."

        return (
            "〽️ <b>Первый ордер на покупку для USDT/A7A5</b>\n\n"
            f"Цена: <code>{self._fmt_num(price)}</code>\n"
            f"Объём: <code>{self._fmt_num(volume)}</code>"
        )