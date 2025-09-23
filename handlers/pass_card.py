# handlers/pass_card.py
from pathlib import Path
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile


class PassCardHandler:
    """
    /проходка — отправить изображение пропуска из images/pass_to_office.jpeg
    """

    def __init__(self, image_path: Path | str = "images/pass_to_office.jpeg") -> None:
        self.router = Router()
        self.image_path = Path(image_path)
        self._register()

    async def _cmd_pass(self, message: Message) -> None:
        if not self.image_path.exists():
            await message.answer("Файл пропуска не найден: images/pass_to_office.jpeg")
            return

        photo = FSInputFile(self.image_path)
        caption = (
            "📍БЦ «Высоцкий»\n"
            "11 этаж, офис 15. \n"
            "Пропуск - на ресепшене по документу.\n\n"
            "🚗 Паркинг \n"
            "Въезд с ул. Красноармейская. \n"
            "Гостевой пропуск оформляется на «офис 11/15», сдаётся при выезде."
        )
        await message.answer_photo(photo=photo, caption=caption)

    def _register(self) -> None:
        self.router.message.register(self._cmd_pass, Command("проходка"))
