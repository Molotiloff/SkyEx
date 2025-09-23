# handlers/clients.py
import html
from typing import Iterable

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from db_asyncpg.repo import Repo
from keyboards.confirm import confirm_kb  # ✅ клавиатура Да/Нет


def _chunk(text: str, limit: int = 3500) -> list[str]:
    out, cur, total = [], [], 0
    for line in text.splitlines(True):
        if total + len(line) > limit and cur:
            out.append("".join(cur))
            cur, total = [], 0
        cur.append(line)
        total += len(line)
    if cur:
        out.append("".join(cur))
    return out


class ClientsHandler:
    """
    /клиенты — список активных клиентов. Доступ: только из admin_chat_ids.
    /rmclient <chat_id> — мягко удалить клиента (is_active=false) с подтверждением.
                          Доступ: только из admin_chat_ids.
    """
    def __init__(self, repo: Repo, admin_chat_ids: Iterable[int] | None = None) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.router = Router()
        self._register()

    # --- Команда: список клиентов ---
    async def _cmd_clients(self, message: Message) -> None:
        # доступ только из админского чата
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Команда доступна только в админском чате.")
            return

        clients = await self.repo.list_clients()  # показывает только активных

        lines: list[str] = [f"<b>Клиенты: {len(clients)}</b>"]
        for c in sorted(clients, key=lambda x: (x.get("name") or "").lower()):
            name = html.escape(c.get("name") or "")
            city = html.escape(c.get("city") or "")
            chat_id = c["chat_id"]

            line = f"{name}"
            if city:
                line += f" — {city}"
            line += f"\n    chat_id = <code>{chat_id}</code>"
            lines.append(line)

        text = "\n".join(lines)
        for chunk in _chunk(text):
            await message.answer(chunk, parse_mode="HTML")

    # --- Команда: мягкое удаление клиента (с подтверждением) ---
    async def _cmd_rmclient(self, message: Message) -> None:
        # доступ только из админского чата
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Команда доступна только в админском чате.")
            return

        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer("Использование: /rmclient <chat_id>")
            return

        try:
            chat_id_to_remove = int(parts[1].strip())
        except ValueError:
            await message.answer("Ошибка: chat_id должен быть числом.")
            return

        # спрашиваем подтверждение
        text = (
            "Подтвердите удаление клиента (мягкое — is_active=false).\n"
            f"chat_id = <code>{chat_id_to_remove}</code>"
        )
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=confirm_kb(
                yes_cb=f"rmcli:{chat_id_to_remove}:yes",
                no_cb=f"rmcli:{chat_id_to_remove}:no",
            ),
        )

    # --- Callback: обработка подтверждения удаления клиента ---
    async def _cb_rmclient(self, cq: CallbackQuery) -> None:
        # доступ только из админского чата
        if self.admin_chat_ids and (not cq.message or cq.message.chat.id not in self.admin_chat_ids):
            await cq.answer("Доступно только в админском чате.", show_alert=True)
            return

        try:
            kind, chat_id_str, answer = (cq.data or "").split(":")
            if kind != "rmcli":
                return
            chat_id_to_remove = int(chat_id_str)
        except Exception:
            await cq.answer("Некорректные данные.", show_alert=True)
            return

        if answer == "no":
            # просто убираем клавиатуру и помечаем отмену
            try:
                old = cq.message.text or ""
                await cq.message.edit_text(old + "\nОтменено.", parse_mode="HTML")
            except Exception:
                pass
            try:
                await cq.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await cq.answer("Отмена")
            return

        # answer == "yes" — мягко удаляем
        try:
            ok = await self.repo.remove_client(chat_id_to_remove)
            if ok:
                new_text = (
                    "Клиент помечен как неактивный (is_active=false).\n"
                    f"chat_id = <code>{chat_id_to_remove}</code>"
                )
            else:
                new_text = (
                    "Клиент не найден или уже неактивен.\n"
                    f"chat_id = <code>{chat_id_to_remove}</code>"
                )

            try:
                await cq.message.edit_text(new_text, parse_mode="HTML")
            except Exception:
                pass
            try:
                await cq.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await cq.answer("Готово")
        except Exception as e:
            await cq.answer(f"Ошибка: {e}", show_alert=True)

    def _register(self) -> None:
        self.router.message.register(self._cmd_clients, Command("клиенты"))
        self.router.message.register(self._cmd_rmclient, Command("rmclient"))
        self.router.callback_query.register(self._cb_rmclient, F.data.startswith("rmcli:"))
