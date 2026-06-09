from __future__ import annotations

from datetime import UTC, date, datetime


class BaseRepo:
    @staticmethod
    def _normalize_dt(v: datetime | date | str | None) -> datetime | None:
        """
        Принимает datetime/date/ISO-строку/None.
        Возвращает timezone-aware datetime (UTC) или None.
        """
        if v is None:
            return None
        if isinstance(v, datetime):
            dt = v
        elif isinstance(v, date):
            dt = datetime(v.year, v.month, v.day)
        elif isinstance(v, str):
            try:
                dt = datetime.fromisoformat(v)
            except ValueError:
                raise ValueError(f"Invalid datetime string: {v!r}") from None
        else:
            raise TypeError("Unsupported datetime type")

        dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        return dt
