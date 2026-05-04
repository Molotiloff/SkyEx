from __future__ import annotations

from db_asyncpg.pool import get_pool
from db_asyncpg.repositories.base import BaseRepo


class ActCounterRepo(BaseRepo):
    async def _ensure_act_request_transactions_table(self, con) -> None:
        await con.execute(
            """
            CREATE TABLE IF NOT EXISTS act_request_transactions (
                id BIGSERIAL PRIMARY KEY,
                req_id TEXT NOT NULL,
                table_req_id TEXT,
                request_chat_id BIGINT NOT NULL,
                request_message_id BIGINT NOT NULL,
                transaction_id BIGINT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
                direction TEXT NOT NULL CHECK (direction IN ('IN', 'OUT')),
                status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CANCELED')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                canceled_at TIMESTAMPTZ
            )
            """
        )
        await con.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_act_request_transactions_transaction_id
            ON act_request_transactions(transaction_id)
            """
        )
        await con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_act_request_transactions_req_id
            ON act_request_transactions(req_id)
            """
        )
        await con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_act_request_transactions_chat_status_created
            ON act_request_transactions(request_chat_id, status, created_at, id)
            """
        )
        await con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_act_request_transactions_table_req_id
            ON act_request_transactions(table_req_id)
            """
        )

    async def link_act_request_transaction(
        self,
        *,
        req_id: str,
        request_chat_id: int,
        request_message_id: int,
        transaction_id: int,
        direction: str,
        table_req_id: str | None = None,
        status: str = "ACTIVE",
    ) -> int:
        direction_norm = str(direction).strip().upper()
        status_norm = str(status).strip().upper()

        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_act_request_transactions_table(con)
                row = await con.fetchrow(
                    """
                    INSERT INTO act_request_transactions (
                        req_id,
                        table_req_id,
                        request_chat_id,
                        request_message_id,
                        transaction_id,
                        direction,
                        status
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (transaction_id) DO UPDATE SET
                        req_id = EXCLUDED.req_id,
                        table_req_id = COALESCE(EXCLUDED.table_req_id, act_request_transactions.table_req_id),
                        request_chat_id = EXCLUDED.request_chat_id,
                        request_message_id = EXCLUDED.request_message_id,
                        direction = EXCLUDED.direction,
                        status = EXCLUDED.status,
                        canceled_at = CASE
                            WHEN EXCLUDED.status = 'CANCELED' THEN now()
                            ELSE NULL
                        END
                    RETURNING id
                    """,
                    str(req_id),
                    str(table_req_id) if table_req_id is not None else None,
                    int(request_chat_id),
                    int(request_message_id),
                    int(transaction_id),
                    direction_norm,
                    status_norm,
                )
                return int(row["id"])

    async def cancel_act_request_transactions(self, *, req_id: str) -> int:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_act_request_transactions_table(con)
                res = await con.execute(
                    """
                    UPDATE act_request_transactions
                    SET status = 'CANCELED',
                        canceled_at = now()
                    WHERE req_id = $1
                      AND status <> 'CANCELED'
                    """,
                    str(req_id),
                )
                return int(res.split()[-1])

    async def get_act_request_transaction(self, *, req_id: str) -> list[dict[str, Any]]:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_act_request_transactions_table(con)
                rows = await con.fetch(
                    """
                    SELECT
                        art.*,
                        t.client_id,
                        t.account_id,
                        t.txn_at,
                        t.amount,
                        t.balance_after,
                        t.comment,
                        t.source,
                        a.currency_code,
                        a.precision
                    FROM act_request_transactions art
                    JOIN transactions t ON t.id = art.transaction_id
                    JOIN client_accounts a ON a.id = t.account_id
                    WHERE art.req_id = $1
                    ORDER BY art.id ASC
                    """,
                    str(req_id),
                )
                return [dict(row) for row in rows]
