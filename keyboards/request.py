from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

CB_PARTNER = "req:partner"
CB_TABLE_DONE = "req:table_done"
CB_ISSUE_DONE = "req:issue_done"


def request_keyboard(
    cb_partner: str = CB_PARTNER,
    cb_table_done: str = CB_TABLE_DONE,
) -> InlineKeyboardMarkup:
    """
    Клавиатура для ЗАЯВОЧНОГО чата:
    - Контрагент
    - Занесена в таблицу
    """
    rows = [
        [InlineKeyboardButton(text="Контрагент", callback_data=cb_partner)],
        [InlineKeyboardButton(text="Занесена в таблицу", callback_data=cb_table_done)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def issue_keyboard(
    req_id: int,
    kind: str,
    cb_issue_done: str = CB_ISSUE_DONE,
) -> InlineKeyboardMarkup:
    """
    Клавиатура для КЛИЕНТСКОГО чата:
    - Кнопка «Выдано» с привязкой к заявке и типом операции.
    callback_data = "req:issue_done:<kind>:<req_id>"
    """
    cb_value = f"{cb_issue_done}:{kind}:{req_id}"
    rows = [[InlineKeyboardButton(text="Выдано", callback_data=cb_value)]]
    return InlineKeyboardMarkup(inline_keyboard=rows)
