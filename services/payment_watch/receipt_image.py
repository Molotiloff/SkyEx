from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _fmt(amount: Decimal) -> str:
    q = amount.quantize(Decimal("0.001"))
    return f"{q:,.3f}".replace(",", "’")


def _pick_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates: list[str] = []
    if bold:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/System/Library/Fonts/Supplemental/Helvetica.ttc",
            ]
        )
    else:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/System/Library/Fonts/Supplemental/Helvetica.ttc",
            ]
        )

    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


@dataclass(frozen=True, slots=True)
class PaymentReceiptImageBuilder:
    width: int = 980
    height: int = 760
    background_color: str = "#18181B"
    plate_color: str = "#2A2930"
    accent_color: str = "#14D97B"
    text_primary: str = "#F4F4F5"
    text_secondary: str = "#B7B7BC"
    divider_color: str = "#3B3A43"

    def build_main_success(
        self,
        *,
        amount: Decimal,
        recipient_address: str,
        tx_hash: str,
    ) -> bytes:
        image = Image.new("RGB", (self.width, self.height), self.background_color)
        draw = ImageDraw.Draw(image)

        amount_font = _pick_font(72, bold=True)
        label_font = _pick_font(38, bold=True)
        value_font = _pick_font(38, bold=True)

        amount_text = f"{_fmt(amount)} USDT"
        top_y = 70
        self._draw_centered_text(draw, y=top_y, text=amount_text, font=amount_font, fill=self.text_primary)

        plate_x1 = 72
        plate_x2 = self.width - 72
        plate_y1 = 230
        plate_y2 = 630
        draw.rounded_rectangle(
            (plate_x1, plate_y1, plate_x2, plate_y2),
            radius=32,
            fill=self.plate_color,
        )

        pad_x = 38
        row_left_x = plate_x1 + pad_x
        row_right_x = plate_x2 - pad_x

        status_y = plate_y1 + 56
        draw.text((row_left_x, status_y), "Статус", font=label_font, fill=self.text_secondary)
        self._draw_right_aligned_text(
            draw,
            x=row_right_x,
            y=status_y,
            text="Завершено",
            font=value_font,
            fill=self.accent_color,
        )

        divider1_y = status_y + 84
        draw.line(
            (plate_x1 + pad_x, divider1_y, plate_x2 - pad_x, divider1_y),
            fill=self.divider_color,
            width=2,
        )

        tx_y = divider1_y + 32
        draw.text((row_left_x, tx_y), "Хэш транзакции", font=label_font, fill=self.text_secondary)
        self._draw_fitted_right_aligned_text(
            draw,
            x=row_right_x,
            y=tx_y,
            text=tx_hash,
            initial_size=38,
            min_size=24,
            fill=self.text_primary,
            max_width=390,
        )

        divider2_y = tx_y + 84
        draw.line(
            (plate_x1 + pad_x, divider2_y, plate_x2 - pad_x, divider2_y),
            fill=self.divider_color,
            width=2,
        )

        recipient_y = divider2_y + 32
        draw.text((row_left_x, recipient_y), "Получатель", font=label_font, fill=self.text_secondary)
        self._draw_fitted_right_aligned_text(
            draw,
            x=row_right_x,
            y=recipient_y,
            text=recipient_address,
            initial_size=38,
            min_size=24,
            fill=self.text_primary,
            max_width=390,
        )

        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    def _draw_centered_text(self, draw: ImageDraw.ImageDraw, *, y: int, text: str, font, fill: str) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        x = (self.width - text_w) // 2
        draw.text((x, y), text, font=font, fill=fill)

    def _draw_right_aligned_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        font,
        fill: str,
    ) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        draw.text((x - text_w, y), text, font=font, fill=fill)

    def _draw_fitted_right_aligned_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        initial_size: int,
        min_size: int,
        fill: str,
        max_width: int,
    ) -> None:
        for size in range(initial_size, min_size - 1, -2):
            font = _pick_font(size, bold=True)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            if text_w <= max_width:
                draw.text((x - text_w, y), text, font=font, fill=fill)
                return

        fallback = f"{text[:10]}...{text[-8:]}" if len(text) > 21 else text
        font = _pick_font(min_size, bold=True)
        bbox = draw.textbbox((0, 0), fallback, font=font)
        text_w = bbox[2] - bbox[0]
        draw.text((x - text_w, y), fallback, font=font, fill=fill)
