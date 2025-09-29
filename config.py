# config.py
import os
from dataclasses import dataclass


@dataclass
class Config:
    bot_token: str
    database_url: str
    admin_chat_id: int
    admin_ids: list[int]
    request_chat_id: int | None
    cash_chat_id: int | None

    @classmethod
    def from_env(cls) -> "Config":
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass

        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Не найден BOT_TOKEN в окружении")

        db_url = os.getenv("DATABASE_URL", "").strip()
        if not db_url:
            raise RuntimeError("Не найден DATABASE_URL в окружении")

        admin_chat_id = int(os.getenv("ADMIN_CHAT_ID", "0"))

        admin_ids = [
            int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
        ]
        req_chat_env = os.getenv("REQUEST_CHAT_ID", "").strip()
        request_chat_id = int(req_chat_env) if req_chat_env else None

        cash_chat_env = os.getenv("CASH_CHAT_ID", "").strip()
        cash_chat_id = int(cash_chat_env) if cash_chat_env else None

        return cls(
            bot_token=token,
            database_url=db_url,
            admin_chat_id=admin_chat_id,
            admin_ids=admin_ids,
            request_chat_id=request_chat_id,
            cash_chat_id=cash_chat_id
        )
