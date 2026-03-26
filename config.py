import os
from dataclasses import dataclass


def _parse_int(s: str | None) -> int | None:
    s = (s or "").strip()
    return int(s) if s else None


def _parse_int_list(s: str | None) -> list[int]:
    if not s:
        return []
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def _parse_ids_set(s: str | None) -> set[int]:
    """
    Формат env:
      CITY_CASH_CHAT_IDS="-4301,-52251,-50"
      SCHEDULE_CHAT_IDS="-1001,-1002"
    """
    if not s:
        return set()
    return {int(x.strip()) for x in s.split(",") if x.strip()}


def _parse_city_chat_map(s: str | None, *, env_name: str) -> dict[str, int]:
    """
    Формат env:
      CASH_CHAT_ID="екб:-49509,члб:-49502"
      CITY_SCHEDULE_CHATS="екб:-60001,члб:-60002"
    """
    out: dict[str, int] = {}
    raw = (s or "").strip()
    if not raw:
        return out

    for part in raw.split(","):
        part = part.strip().strip('"').strip("'")
        if not part:
            continue
        if ":" not in part:
            raise RuntimeError(
                f"Некорректный формат {env_name}. Ожидаю 'екб:-100...,члб:-100...'"
            )
        city, chat_id = part.split(":", 1)
        city = (city or "").strip().lower()
        chat_id = (chat_id or "").strip()
        if not city or not chat_id:
            continue
        out[city] = int(chat_id)
    return out


@dataclass(slots=True)
class Config:
    bot_token: str
    database_url: str

    admin_chat_id: int
    admin_ids: list[int]

    # общий чат заявок (legacy)
    request_chat_id: int | None

    # город -> чат заявок
    cash_chat_map: dict[str, int]

    # город -> чат расписания
    city_schedule_chats: dict[str, int]

    # чаты для автоотчётов scheduler
    schedule_chat_ids: set[int]

    # чат для ордеров по курсу
    rate_orders_chat_id: int | None

    default_city: str  # например "екб"

    # кассы города (операционные чаты кассиров)
    city_cash_chat_ids: set[int]

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
        admin_ids = _parse_int_list(os.getenv("ADMIN_IDS"))

        request_chat_id = _parse_int(os.getenv("REQUEST_CHAT_ID"))

        cash_chat_map = _parse_city_chat_map(
            os.getenv("CASH_CHAT_ID"),
            env_name="CASH_CHAT_ID",
        )

        city_schedule_chats = _parse_city_chat_map(
            os.getenv("CITY_SCHEDULE_CHATS"),
            env_name="CITY_SCHEDULE_CHATS",
        )

        schedule_chat_ids = _parse_ids_set(os.getenv("SCHEDULE_CHAT_IDS"))
        rate_orders_chat_id = _parse_int(os.getenv("RATE_ORDERS_CHAT_ID"))

        default_city = (os.getenv("DEFAULT_CITY", "екб") or "екб").strip().lower()

        city_cash_chat_ids = _parse_ids_set(os.getenv("CITY_CASH_CHAT_IDS"))

        return cls(
            bot_token=token,
            database_url=db_url,
            admin_chat_id=admin_chat_id,
            admin_ids=admin_ids,
            request_chat_id=request_chat_id,
            cash_chat_map=cash_chat_map,
            city_schedule_chats=city_schedule_chats,
            schedule_chat_ids=schedule_chat_ids,
            rate_orders_chat_id=rate_orders_chat_id,
            default_city=default_city,
            city_cash_chat_ids=city_cash_chat_ids,
        )