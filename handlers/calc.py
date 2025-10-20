from __future__ import annotations

from decimal import Decimal, InvalidOperation
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from uuid import uuid4
from utils.calc import evaluate, CalcError


def _fmt_decimal_smart(d: Decimal) -> str:
    """
    Красиво формируем число:
    - если целое -> без дробной части
    - иначе -> до 8 знаков, без хвостовых нулей
    """
    s = f"{d.normalize():f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".") or "0"
    # ограничим вывод до разумной длины (до 8 знаков после точки)
    if "." in s:
        head, tail = s.split(".", 1)
        tail = tail[:8].rstrip("0")
        s = head if not tail else f"{head}.{tail}"
    return s


async def _cmd_calc(message: Message) -> None:
    text = message.text or ""
    expr = text[len("/calc"):].strip() if text.startswith("/calc") else ""
    if not expr:
        await message.answer("Использование: /calc <выражение>\nНапример: /calc (2+3)*100-50%")
        return
    try:
        result = evaluate(expr)
        sres = _fmt_decimal_smart(result)
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
        sres = _fmt_decimal_smart(result)
        await message.answer(f"{expr} = {sres}")
    except CalcError as e:
        await message.answer(f"Ошибка: {e}")


# ---------- INLINE MODE ----------
async def _on_inline(q: InlineQuery) -> None:
    query = (q.query or "").strip()

    # пустой запрос — подсказка
    if not query:
        hint = "Введите выражение, напр.: (2+3)*10 - 50%"
        await q.answer(
            results=[
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="Калькулятор — введите выражение",
                    description=hint,
                    input_message_content=InputTextMessageContent(
                        message_text="Калькулятор: введите выражение после @бота, напр.: <code>(2+3)*10-50%</code>",
                        parse_mode="HTML",
                    ),
                )
            ],
            is_personal=True,
            cache_time=1,
        )
        return

    # считаем
    try:
        value = evaluate(query)  # Decimal
    except (CalcError, InvalidOperation) as e:
        await q.answer(
            results=[
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="Ошибка в выражении",
                    description=str(e),
                    input_message_content=InputTextMessageContent(
                        message_text=f"❌ Ошибка в выражении: <code>{e}</code>",
                        parse_mode="HTML",
                    ),
                )
            ],
            is_personal=True,
            cache_time=1,
        )
        return

    pretty = _fmt_decimal_smart(value)
    # Вариант 1: <expr> = <result>
    res_full = InlineQueryResultArticle(
        id=str(uuid4()),
        title=f"= {pretty}",
        description=f"{query} = {pretty}",
        input_message_content=InputTextMessageContent(
            message_text=f"<code>{query}</code> = <b>{pretty}</b>",
            parse_mode="HTML",
        ),
    )
    # Вариант 2: только число
    res_num = InlineQueryResultArticle(
        id=str(uuid4()),
        title=f"Только число: {pretty}",
        description="Отправить только результат",
        input_message_content=InputTextMessageContent(
            message_text=f"{pretty}",
        ),
    )
    # Вариант 3: деньги с 2 знаками + исходное выражение
    money2 = value.quantize(Decimal("0.01"))
    res_money2 = InlineQueryResultArticle(
        id=str(uuid4()),
        title=f"Деньги (2 знака): {money2}",
        description=f"{query} = {money2}",
        input_message_content=InputTextMessageContent(
            message_text=f"<code>{query}</code> = <b>{money2}</b>",
            parse_mode="HTML",
        ),
    )

    await q.answer(
        results=[res_full, res_num, res_money2],
        is_personal=True,
        cache_time=1,
        switch_pm_text="Открыть чат с ботом",
        switch_pm_parameter="inline",
    )


class CalcHandler:
    def __init__(self) -> None:
        self.router = Router()
        self._register()

    def _register(self) -> None:
        # команды
        self.router.message.register(_cmd_calc, Command("calc"))
        # любой слэш, который не команда (после / не буква)
        self.router.message.register(_slash_calc, F.text.regexp(r"^/[+\-0-9(]"))
        # inline
        self.router.inline_query.register(_on_inline, F.query.regexp(r".*"))