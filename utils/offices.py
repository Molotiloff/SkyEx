# utils/offices.py
from __future__ import annotations

from pathlib import Path
from handlers.office_cards import OfficeCard

OFFICE_CARDS: dict[str, OfficeCard] = {
    "екб": OfficeCard(
        command="екб",
        image_path=Path("images/ekb_office.jpeg"),
        caption=(
            "📍 БЦ «Высоцкий»\n"
            "11 этаж, офис 15.\n"
            "Пропуск — на ресепшене по документу.\n\n"
            "🚗 Паркинг\n"
            "Въезд с ул. Красноармейская.\n"
            "Гостевой пропуск оформляется на «офис 11/15», сдаётся при выезде."
        ),
    ),
    "члб": OfficeCard(
        command="члб",
        image_path=Path("images/chlb_office.jpeg"),
        caption=(
            "📍 Челябинск — адрес офиса\n"
            "Этаж/офис ...\n"
            "Пропуск — ...\n\n"
            "🚗 Паркинг\n"
            "Въезд — ...\n"
        ),
    ),
}