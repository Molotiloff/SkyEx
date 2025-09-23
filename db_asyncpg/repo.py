# app/db_asyncpg/repo.py
from __future__ import annotations

from decimal import Decimal
from typing import Any

from db_asyncpg.pool import get_pool
from db_asyncpg.utils import to_upper, quantize_amount


class Repo:
    """
    Хранилище для клиентов (телеграм-чаты), счетов (валюты) и транзакций.
    Работает поверх asyncpg-пула. Все операции изменения баланса атомарны.
    """

    # ---------- Клиенты ----------
    async def ensure_client(self, chat_id: int, name: str, city: str | None = None) -> int:
        """
        Вернёт client_id, создаст при отсутствии. При изменении name/city — обновляет.
        """
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT id, name, city FROM clients WHERE chat_id=$1",
                    chat_id,
                )
                if row:
                    # при изменении — обновим «для справки»
                    if (name and row["name"] != name) or (city is not None and row["city"] != city):
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

    async def add_currency(self, client_id: int, currency_code: str, precision: int) -> int:
        """
        Активирует/создаёт счёт клиента в валюте. Возвращает account_id.
        """
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
        """
        Мягкое удаление счёта (is_active=false).
        Теперь разрешено даже при ненулевом балансе.
        """
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
                # asyncpg возвращает строки вида 'UPDATE 0' / 'UPDATE 1'
                return res.endswith(" 1")

    async def snapshot_wallet(self, client_id: int) -> list[dict[str, Any]]:
        """
        Список активных счетов клиента с балансами.
        """
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
            amount: Decimal | str | int | float,  # знаковая величина (+ пополнение, − списание)
            group_id: int | None = None,
            actor_id: int | None = None,
            comment: str | None = None,
            source: str | None = None,
            txn_at: str | None = None,  # ISO8601, None → NOW()
            idempotency_key: str | None = None,
    ) -> int:
        """
        Базовый метод изменения баланса счёта с записью транзакции.
        Возвращает transaction_id. Идемпотентность опциональна (по client_id + idempotency_key).
        """
        code = to_upper(currency_code)
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                # 1) Идемпотентность (если ключ передан)
                if idempotency_key:
                    exist = await con.fetchrow(
                        "SELECT id FROM transactions WHERE client_id=$1 AND idempotency_key=$2",
                        client_id, idempotency_key,
                    )
                    if exist:
                        return exist["id"]

                # 2) Блокируем счёт
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

                # 3) Обновляем баланс
                await con.execute(
                    "UPDATE client_accounts SET balance=$1 WHERE id=$2",
                    new_balance, acc["id"],
                )

                # 4) Записываем транзакцию
                rec = await con.fetchrow(
                    """
                    INSERT INTO transactions
                      (client_id, account_id, txn_at, amount, balance_after,
                       group_id, actor_id, comment, source, idempotency_key)
                    VALUES ($1, $2, COALESCE($3::timestamptz, NOW()), $4, $5, $6, $7, $8, $9, $10)
                    RETURNING id
                    """,
                    client_id, acc["id"], txn_at, qamount, new_balance,
                    group_id, actor_id, comment, source, idempotency_key,
                )
                return rec["id"]

    async def deposit(self, **kwargs) -> int:
        """
        Пополнение (amount > 0). amount может быть str/int/float/Decimal.
        """
        return await self._apply_delta(**kwargs)

    async def withdraw(self, **kwargs) -> int:
        """
        Списание (amount > 0 на входе — будет инвертирован до отрицательного).
        """
        if "amount" in kwargs:
            kwargs = dict(kwargs)
            kwargs["amount"] = -Decimal(str(kwargs["amount"]))
        return await self._apply_delta(**kwargs)

    async def history(
            self,
            account_id: int,
            *,
            limit: int = 50,
            since: str | None = None,  # ISO8601 (включительно)
            until: str | None = None,  # ISO8601 (исключительно)
            cursor_txn_at: str | None = None,
            cursor_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Выписка по счёту. Поддерживает период и кейсет-пагинацию по (txn_at, id).
        """
        pool = await get_pool()
        async with pool.acquire() as con:
            where = ["account_id = $1"]
            params: list[Any] = [account_id]

            if since is not None:
                where.append("txn_at >= $%d" % (len(params) + 1))
                params.append(since)
            if until is not None:
                where.append("txn_at <  $%d" % (len(params) + 1))
                params.append(until)
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
        """
        Сколько денег у клиентов по валютам (моментальный снимок из accounts),
        включая precision счёта для корректного форматирования.
        """
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

    async def list_clients(self) -> list[dict]:
        """
        Список всех клиентов (чатов) с количеством счетов.
        """
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
                GROUP BY c.id
                ORDER BY c.created_at DESC, c.id DESC
                """
            )
            return [dict(r) for r in rows]

    async def set_client_city_by_chat_id(self, chat_id: int, city: str) -> dict | None:
        """
        Установить/обновить city для клиента по chat_id.
        Возвращает словарь с полями клиента или None, если не найден.
        """
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
            since: str | None = None,
            until: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Плоский набор строк для экспорта в Google-таблицу/документ.
        """
        pool = await get_pool()
        async with pool.acquire() as con:
            where = ["TRUE"]
            params: list[Any] = []
            if client_id is not None:
                where.append("client_id = $%d" % (len(params) + 1))
                params.append(client_id)
            if since is not None:
                where.append("txn_at >= $%d" % (len(params) + 1))
                params.append(since)
            if until is not None:
                where.append("txn_at <  $%d" % (len(params) + 1))
                params.append(until)

            sql = f"""
                SELECT t.id, t.client_id, c.name AS client_name, c.chat_id,
                       t.account_id, a.currency_code,
                       t.txn_at, t.amount, t.balance_after,
                       t.group_id, g.name AS group_name,
                       t.actor_id, ac.display_name AS actor_name,
                       t.comment, t.source
                FROM transactions t
                JOIN clients c ON c.id = t.client_id
                JOIN client_accounts a ON a.id = t.account_id
                LEFT JOIN txn_groups g ON g.id = t.group_id
                LEFT JOIN actors ac    ON ac.id = t.actor_id
                WHERE {' AND '.join(where)}
                ORDER BY t.txn_at, t.id
            """
            rows = await con.fetch(sql, *params)
            return [dict(r) for r in rows]

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
                        value=EXCLUDED.value,
                        updated_at=now()
                    """,
                    key, value,
                )
