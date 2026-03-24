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
    async def update_client_chat_id(self, *, client_id: int, new_chat_id: int) -> None:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                # Если вдруг новый chat_id уже занят другой записью — лучше явно упасть с понятной ошибкой.
                exist = await con.fetchrow(
                    "SELECT id FROM clients WHERE chat_id=$1 AND id<>$2",
                    int(new_chat_id), int(client_id),
                )
                if exist:
                    raise ValueError(f"chat_id {new_chat_id} already belongs to client_id={exist['id']}")

                await con.execute(
                    "UPDATE clients SET chat_id=$1 WHERE id=$2",
                    int(new_chat_id), int(client_id),
                )

    async def find_client_by_name_exact(self, name: str) -> dict[str, Any] | None:
        pool = await get_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                """
                SELECT id, chat_id, name, client_group
                FROM clients
                WHERE is_active = TRUE
                  AND name = $1
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                name.strip(),
            )
            return dict(row) if row else None

    async def ensure_client(self, chat_id: int, name: str, client_group: str | None = None) -> int:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT id, name, client_group, is_active FROM clients WHERE chat_id=$1",
                    int(chat_id),
                )
                if row:
                    need_update_ng = (
                            (name and row["name"] != name)
                            or (client_group is not None and row["client_group"] != client_group)
                    )

                    if not row["is_active"]:
                        await con.execute(
                            """
                            UPDATE clients
                            SET is_active = TRUE,
                                deactivated_at = NULL,
                                name = COALESCE($2, name),
                                client_group = COALESCE($3, client_group)
                            WHERE id = $1
                            """,
                            int(row["id"]), name, client_group,
                        )
                    elif need_update_ng:
                        await con.execute(
                            """
                            UPDATE clients
                            SET name = COALESCE($2, name),
                                client_group = COALESCE($3, client_group)
                            WHERE id = $1
                            """,
                            int(row["id"]), name, client_group,
                        )
                    return int(row["id"])

                by_name = await con.fetchrow(
                    """
                    SELECT id, chat_id, name, client_group, is_active
                    FROM clients
                    WHERE is_active = TRUE
                      AND name = $1
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (name or "").strip(),
                )
                if by_name:
                    await con.execute(
                        """
                        UPDATE clients
                        SET chat_id = $1,
                            client_group = COALESCE($2, client_group)
                        WHERE id = $3
                        """,
                        int(chat_id), client_group, int(by_name["id"]),
                    )
                    return int(by_name["id"])

                rec = await con.fetchrow(
                    """
                    INSERT INTO clients(chat_id, name, client_group)
                    VALUES($1, $2, $3)
                    RETURNING id
                    """,
                    int(chat_id), name, client_group,
                )
                return int(rec["id"])

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
                    c.client_group,
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
                    c.client_group,
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

    async def set_client_group_by_chat_id(self, chat_id: int, client_group: str) -> dict | None:
        pool = await get_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                """
                UPDATE clients
                SET client_group = $2
                WHERE chat_id = $1
                RETURNING id, chat_id, name, client_group, created_at
                """,
                chat_id, client_group.strip(),
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

    # ---------- Выписки/экспорт ----------

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

    # ---------- Настройки (app_settings) ----------

    async def _ensure_settings_table(self, con) -> None:
        await con.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

    async def get_setting(self, key: str) -> str | None:
        pool = await get_pool()
        async with pool.acquire() as con:
            await self._ensure_settings_table(con)
            row = await con.fetchrow("SELECT value FROM app_settings WHERE key=$1", key)
            return None if row is None else str(row["value"])

    async def set_setting(self, key: str, value: str) -> None:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_settings_table(con)
                await con.execute(
                    """
                    INSERT INTO app_settings(key, value)
                    VALUES ($1, $2)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = now()
                    """,
                    key, value,
                )

    async def _ensure_request_schedule_table(self, con) -> None:
        await con.execute(
            """
            CREATE TABLE IF NOT EXISTS request_schedule_entries (
                id BIGSERIAL PRIMARY KEY,
                req_id TEXT NOT NULL,
                city TEXT NOT NULL,
                hhmm TEXT,
                request_kind TEXT NOT NULL,
                line_text TEXT NOT NULL,
                client_name TEXT NOT NULL,
                request_chat_id BIGINT NOT NULL,
                request_message_id BIGINT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (req_id)
            )
            """
        )
        await con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_request_schedule_entries_city_active_hhmm
            ON request_schedule_entries(city, is_active, hhmm)
            """
        )
        await con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_request_schedule_entries_request_msg
            ON request_schedule_entries(request_chat_id, request_message_id)
            """
        )

    async def upsert_request_schedule_entry(
            self,
            *,
            req_id: str,
            city: str,
            hhmm: str | None,
            request_kind: str,
            line_text: str,
            client_name: str,
            request_chat_id: int,
            request_message_id: int,
    ) -> None:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_request_schedule_table(con)
                await con.execute(
                    """
                    INSERT INTO request_schedule_entries (
                        req_id,
                        city,
                        hhmm,
                        request_kind,
                        line_text,
                        client_name,
                        request_chat_id,
                        request_message_id,
                        is_active,
                        updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE, now())
                    ON CONFLICT (req_id) DO UPDATE SET
                        city = EXCLUDED.city,
                        hhmm = EXCLUDED.hhmm,
                        request_kind = EXCLUDED.request_kind,
                        line_text = EXCLUDED.line_text,
                        client_name = EXCLUDED.client_name,
                        request_chat_id = EXCLUDED.request_chat_id,
                        request_message_id = EXCLUDED.request_message_id,
                        is_active = TRUE,
                        updated_at = now()
                    """,
                    req_id,
                    city.strip().lower(),
                    (hhmm or None),
                    request_kind,
                    line_text,
                    client_name,
                    int(request_chat_id),
                    int(request_message_id),
                )

    async def list_request_schedule_entries(
            self,
            *,
            city: str,
    ) -> list[dict[str, Any]]:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_request_schedule_table(con)
                rows = await con.fetch(
                    """
                    SELECT
                        req_id,
                        city,
                        hhmm,
                        request_kind,
                        line_text,
                        client_name,
                        request_chat_id,
                        request_message_id,
                        is_active,
                        created_at,
                        updated_at
                    FROM request_schedule_entries
                    WHERE city = $1
                      AND is_active = TRUE
                    ORDER BY hhmm ASC, updated_at ASC, id ASC
                    """,
                    city.strip().lower(),
                )
                return [dict(r) for r in rows]

    async def deactivate_request_schedule_entry(self, req_id: str) -> bool:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_request_schedule_table(con)
                res = await con.execute(
                    """
                    UPDATE request_schedule_entries
                    SET is_active = FALSE,
                        updated_at = now()
                    WHERE req_id = $1
                      AND is_active = TRUE
                    """,
                    req_id,
                )
                return res.endswith(" 1")

    async def _ensure_request_schedule_boards_table(self, con) -> None:
        await con.execute(
            """
            CREATE TABLE IF NOT EXISTS request_schedule_boards (
                city TEXT PRIMARY KEY,
                board_chat_id BIGINT NOT NULL,
                board_message_id BIGINT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

    async def get_request_schedule_board(self, *, city: str) -> dict[str, Any] | None:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_request_schedule_boards_table(con)
                row = await con.fetchrow(
                    """
                    SELECT city, board_chat_id, board_message_id, updated_at
                    FROM request_schedule_boards
                    WHERE city = $1
                    """,
                    city.strip().lower(),
                )
                return dict(row) if row else None

    async def upsert_request_schedule_board(
            self,
            *,
            city: str,
            board_chat_id: int,
            board_message_id: int,
    ) -> None:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_request_schedule_boards_table(con)
                await con.execute(
                    """
                    INSERT INTO request_schedule_boards (
                        city,
                        board_chat_id,
                        board_message_id,
                        updated_at
                    )
                    VALUES ($1, $2, $3, now())
                    ON CONFLICT (city) DO UPDATE SET
                        board_chat_id = EXCLUDED.board_chat_id,
                        board_message_id = EXCLUDED.board_message_id,
                        updated_at = now()
                    """,
                    city.strip().lower(),
                    int(board_chat_id),
                    int(board_message_id),
                )

    async def deactivate_request_schedule_entry_by_message(
            self,
            *,
            request_chat_id: int,
            request_message_id: int,
    ) -> bool:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_request_schedule_table(con)
                res = await con.execute(
                    """
                    UPDATE request_schedule_entries
                    SET is_active = FALSE,
                        updated_at = now()
                    WHERE request_chat_id = $1
                      AND request_message_id = $2
                      AND is_active = TRUE
                    """,
                    int(request_chat_id),
                    int(request_message_id),
                )
                return not res.endswith(" 0")

    async def get_request_schedule_entry_by_message(
            self,
            *,
            request_chat_id: int,
            request_message_id: int,
    ) -> dict[str, Any] | None:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_request_schedule_table(con)
                row = await con.fetchrow(
                    """
                    SELECT
                        req_id,
                        city,
                        hhmm,
                        request_kind,
                        line_text,
                        client_name,
                        request_chat_id,
                        request_message_id,
                        is_active,
                        created_at,
                        updated_at
                    FROM request_schedule_entries
                    WHERE request_chat_id = $1
                      AND request_message_id = $2
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                    """,
                    int(request_chat_id),
                    int(request_message_id),
                )
                return dict(row) if row else None
