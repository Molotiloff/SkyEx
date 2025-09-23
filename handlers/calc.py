# handlers/calc.py
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from utils.calc import evaluate, CalcError


async def _cmd_calc(message: Message) -> None:
    text = message.text or ""
    expr = text[len("/calc"):].strip() if text.startswith("/calc") else ""
    if not expr:
        await message.answer("Использование: /calc <выражение>\nНапример: /calc (2+3)*100-50%")
        return
    try:
        result = evaluate(expr)
        sres = f"{result.normalize():f}"
        if "." in sres:
            sres = sres.rstrip("0").rstrip(".") or "0"
        await message.answer(f"{expr} = {sres}")
    except CalcError as e:
        await message.answer(f"Ошибка: {e}")


async def _slash_calc(message: Message) -> None:
    raw = (message.text or "").strip()
    expr = raw[1:].strip()  # убираем первый '/'
    if not expr:
        return
    try:
        result = evaluate(expr)
        sres = f"{result.normalize():f}"
        if "." in sres:
            sres = sres.rstrip("0").rstrip(".") or "0"
        await message.answer(f"{expr} = {sres}")
    except CalcError as e:
        await message.answer(f"Ошибка: {e}")


class CalcHandler:
    def __init__(self) -> None:
        self.router = Router()
        self._register()

    def _register(self) -> None:
        self.router.message.register(_cmd_calc, Command("calc"))
        # любой слэш, который не команда (после / не буква)
        self.router.message.register(_slash_calc, F.text.regexp(r"^/[+\-0-9(]"))
