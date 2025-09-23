# keyboards/main.py
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


class MainKeyboard:
    @staticmethod
    def main() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="/помоги")],
                [KeyboardButton(text="/дай"), KeyboardButton(text="/кош")],
            ],
            resize_keyboard=True,
            input_field_placeholder="Выберите команду"
        )
