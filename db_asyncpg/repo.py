from __future__ import annotations

from decimal import Decimal
from typing import Any
from datetime import datetime, date, timezone

from db_asyncpg.pool import get_pool
from db_asyncpg.utils import to_upper, quantize_amount


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
        # поддерживаем ISO 8601
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


class Repo:
    """
    Хранилище для клиентов (телеграм-чаты), счетов (валюты) и транзакций.
    Работает поверх asyncpg-пула. Все операции изменения баланса атомарны.
    """

    # ---------- Клиенты ----------
    async def ensure_client(self, chat_id: int, name: str, city: str | None = None) -> int:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT id, name, city, is_active FROM clients WHERE chat_id=$1",
                    chat_id,
                )
                if row:
                    need_update_nc = (name and row["name"] != name) or (city is not None and row["city"] != city)
                    if not row["is_active"]:
                        await con.execute(
                            """
                            UPDATE clients
                            SET is_active = TRUE,
                                deactivated_at = NULL,
                                name = COALESCE($2, name),
                                city = COALESCE($3, city)
                            WHERE id = $1
                            """,
                            row["id"], name, city,
                        )
                    elif need_update_nc:
                        await con.execute(
                            "UPDATE clients SET name=COALESCE($2,name), city=COALESCE($3,city) WHERE id=$1",
                            row["id"], name, city,
                        )
                    return row["id"]

                rec = await con.fetchrow(
                    """
                    INSERT INTO clients(chat_id, name, city)
                    VALUES($1, $2, $3)
                    RETURNING id
                    """,
                    chat_id, name, city,
                )
                return rec["id"]

    async def remove_client(self, chat_id: int) -> bool:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE clients
                    SET is_active = FALSE,
                        deactivated_at = NOW()
                    WHERE chat_id = $1
                      AND is_active = TRUE
                    """,
                    chat_id,
                )
                return res.endswith(" 1")

    async def list_clients(self) -> list[dict]:
        pool = await get_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                """
                SELECT
                    c.id,
                    c.chat_id,
                    c.name,
                    c.city,
                    c.created_at,
                    COUNT(a.*)               AS accounts_total,
                    COUNT(a.*) FILTER (WHERE a.is_active) AS accounts_active
                FROM clients c
                LEFT JOIN client_accounts a ON a.client_id = c.id
                WHERE c.is_active = TRUE
                GROUP BY c.id
                ORDER BY c.created_at DESC, c.id DESC
                """
            )
            return [dict(r) for r in rows]

    async def add_currency(self, client_id: int, currency_code: str, precision: int) -> int:
        code = to_upper(currency_code)
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                rec = await con.fetchrow(
                    """
                    INSERT INTO client_accounts(client_id, currency_code, precision)
                    VALUES($1, $2, $3)
                    ON CONFLICT (client_id, currency_code)
                    DO UPDATE SET is_active = TRUE, precision = EXCLUDED.precision, deactivated_at = NULL
                    RETURNING id
                    """,
                    client_id, code, precision,
                )
                return rec["id"]

    async def remove_currency(self, client_id: int, currency_code: str) -> bool:
        code = to_upper(currency_code)
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE client_accounts
                    SET is_active = FALSE,
                        deactivated_at = NOW()
                    WHERE client_id = $1
                      AND currency_code = $2
                      AND is_active = TRUE
                    """,
                    client_id, code,
                )
                return res.endswith(" 1")

    async def snapshot_wallet(self, client_id: int) -> list[dict[str, Any]]:
        pool = await get_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                """
                SELECT id, currency_code, precision, balance
                FROM client_accounts
                WHERE client_id=$1 AND is_active=TRUE
                ORDER BY currency_code
                """,
                client_id,
            )
            return [dict(r) for r in rows]

    # ---------- Транзакции ----------

    async def _apply_delta(
        self,
        *,
        client_id: int,
        currency_code: str,
        amount: Decimal | str | int | float,
        group_id: int | None = None,
        actor_id: int | None = None,
        comment: str | None = None,
        source: str | None = None,
        txn_at: str | datetime | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        code = to_upper(currency_code)
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                if idempotency_key:
                    exist = await con.fetchrow(
                        "SELECT id FROM transactions WHERE client_id=$1 AND idempotency_key=$2",
                        client_id, idempotency_key,
                    )
                    if exist:
                        return exist["id"]

                acc = await con.fetchrow(
                    """
                    SELECT id, precision, balance
                    FROM client_accounts
                    WHERE client_id=$1 AND currency_code=$2 AND is_active=TRUE
                    FOR UPDATE
                    """,
                    client_id, code,
                )
                if not acc:
                    raise KeyError("account not found")

                prec = int(acc["precision"]) if acc["precision"] is not None else 2
                qamount = quantize_amount(amount, prec)
                new_balance = quantize_amount(Decimal(acc["balance"]) + qamount, prec)

                await con.execute(
                    "UPDATE client_accounts SET balance=$1 WHERE id=$2",
                    new_balance, acc["id"],
                )

                # нормализуем txn_at (может быть None/str/datetime)
                txn_at_norm = _normalize_dt(txn_at) if txn_at is not None else None

                rec = await con.fetchrow(
                    """
                    INSERT INTO transactions
                      (client_id, account_id, txn_at, amount, balance_after,
                       group_id, actor_id, comment, source, idempotency_key)
                    VALUES ($1, $2, COALESCE($3::timestamptz, NOW()), $4, $5, $6, $7, $8, $9, $10)
                    RETURNING id
                    """,
                    client_id, acc["id"], txn_at_norm, qamount, new_balance,
                    group_id, actor_id, comment, source, idempotency_key,
                )
                return rec["id"]

    async def deposit(self, **kwargs) -> int:
        return await self._apply_delta(**kwargs)

    async def withdraw(self, **kwargs) -> int:
        if "amount" in kwargs:
            kwargs = dict(kwargs)
            kwargs["amount"] = -Decimal(str(kwargs["amount"]))
        return await self._apply_delta(**kwargs)

    async def history(
        self,
        account_id: int,
        *,
        limit: int = 50,
        since: str | datetime | None = None,
        until: str | datetime | None = None,
        cursor_txn_at: str | None = None,
        cursor_id: int | None = None,
    ) -> list[dict[str, Any]]:
        pool = await get_pool()
        async with pool.acquire() as con:
            where = ["account_id = $1"]
            params: list[Any] = [account_id]

            if since is not None:
                where.append("txn_at >= $%d" % (len(params) + 1))
                params.append(_normalize_dt(since))
            if until is not None:
                where.append("txn_at <  $%d" % (len(params) + 1))
                params.append(_normalize_dt(until))
            if cursor_txn_at is not None and cursor_id is not None:
                where.append("(txn_at, id) < ($%d::timestamptz, $%d)" % (len(params) + 1, len(params) + 2))
                params.extend([cursor_txn_at, cursor_id])

            sql = f"""
                SELECT id, txn_at, amount, balance_after, group_id, actor_id, comment, source
                FROM transactions
                WHERE {' AND '.join(where)}
                ORDER BY txn_at DESC, id DESC
                LIMIT $%d
            """ % (len(params) + 1)
            params.append(limit)

            rows = await con.fetch(sql, *params)
            return [dict(r) for r in rows]

    # ---------- Агрегаты/выборки ----------

    async def balances_by_client(self) -> list[dict[str, Any]]:
        pool = await get_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                """
                SELECT
                    a.client_id,
                    c.name AS client_name,
                    c.chat_id,
                    a.currency_code,
                    a.balance,
                    a.precision
                FROM client_accounts a
                JOIN clients c ON c.id = a.client_id
                WHERE a.is_active = TRUE
                ORDER BY a.client_id, a.currency_code
                """
            )
            return [dict(r) for r in rows]

    async def set_client_city_by_chat_id(self, chat_id: int, city: str) -> dict | None:
        pool = await get_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                """
                UPDATE clients
                SET city = $2
                WHERE chat_id = $1
                RETURNING id, chat_id, name, city, created_at
                """,
                chat_id, city.strip(),
            )
            return dict(row) if row else None

    async def list_managers(self) -> list[dict]:
        pool = await get_pool()
        async with pool.acquire() as con:
            rows = await con.fetch("SELECT user_id, display_name, added_at FROM managers ORDER BY added_at")
            return [dict(r) for r in rows]

    async def add_manager(self, user_id: int, display_name: str = "") -> bool:
        pool = await get_pool()
        async with pool.acquire() as con:
            res = await con.execute(
                """
                INSERT INTO managers (user_id, display_name)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE
                  SET display_name = EXCLUDED.display_name
                """,
                user_id, display_name,
            )
            return res.startswith("INSERT") or res.startswith("UPDATE")

    async def remove_manager(self, user_id: int) -> bool:
        pool = await get_pool()
        async with pool.acquire() as con:
            res = await con.execute("DELETE FROM managers WHERE user_id=$1", user_id)
            return res.endswith(" 1")

    async def is_manager(self, user_id: int) -> bool:
        pool = await get_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow("SELECT 1 FROM managers WHERE user_id=$1", user_id)
            return row is not None

    async def export_transactions(
        self,
        *,
        client_id: int | None = None,
        since: datetime | date | str | None = None,
        until: datetime | date | str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Плоский набор строк для экспорта/выписки.
        Поддерживает since/until как datetime/date/ISO-строку/None.
        """
        since_dt = _normalize_dt(since) if since is not None else None
        until_dt = _normalize_dt(until) if until is not None else None

        pool = await get_pool()
        async with pool.acquire() as con:
            where = ["TRUE"]
            params: list[Any] = []
            if client_id is not None:
                where.append("t.client_id = $%d" % (len(params) + 1))
                params.append(client_id)
            if since_dt is not None:
                where.append("t.txn_at >= $%d" % (len(params) + 1))
                params.append(since_dt)
            if until_dt is not None:
                where.append("t.txn_at <  $%d" % (len(params) + 1))
                params.append(until_dt)

            sql = f"""
                SELECT
                    t.id,
                    t.client_id,
                    c.name      AS client_name,
                    c.chat_id,
                    t.account_id,
                    a.currency_code,
                    t.txn_at,
                    t.amount,
                    t.balance_after,
                    t.group_id,
                    g.name      AS group_name,
                    t.actor_id,
                    ac.display_name AS actor_name,
                    t.comment,
                    t.source
                FROM transactions t
                JOIN clients         c  ON c.id = t.client_id
                JOIN client_accounts a  ON a.id = t.account_id
                LEFT JOIN txn_groups g  ON g.id = t.group_id
                LEFT JOIN actors     ac ON ac.id = t.actor_id
                WHERE {' AND '.join(where)}
                ORDER BY t.txn_at, t.id
            """
            rows = await con.fetch(sql, *params)
            return [dict(r) for r in rows]