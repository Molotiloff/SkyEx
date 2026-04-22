from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from db_asyncpg.pool import get_pool
from db_asyncpg.repositories.base import BaseRepo
from db_asyncpg.utils import quantize_amount, to_upper


class TransactionsRepo(BaseRepo):
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

                txn_at_norm = self._normalize_dt(txn_at) if txn_at is not None else None

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
                params.append(self._normalize_dt(since))
            if until is not None:
                where.append("txn_at <  $%d" % (len(params) + 1))
                params.append(self._normalize_dt(until))
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

    async def export_transactions(
        self,
        *,
        client_id: int | None = None,
        since: datetime | date | str | None = None,
        until: datetime | date | str | None = None,
    ) -> list[dict[str, Any]]:
        since_dt = self._normalize_dt(since) if since is not None else None
        until_dt = self._normalize_dt(until) if until is not None else None

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
