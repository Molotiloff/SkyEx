from __future__ import annotations

from datetime import date, datetime, timezone


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
                raise ValueError(f"Invalid datetime string: {v!r}")
        else:
            raise TypeError("Unsupported datetime type")

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
