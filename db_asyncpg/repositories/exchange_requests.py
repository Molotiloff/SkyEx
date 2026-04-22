from __future__ import annotations

from typing import Any

from db_asyncpg.pool import get_pool


class ExchangeRequestsRepo:
    async def _ensure_exchange_request_links_table(self, con) -> None:
        await con.execute(
            """
            CREATE TABLE IF NOT EXISTS exchange_request_links (
                client_req_id TEXT PRIMARY KEY,
                table_req_id TEXT NOT NULL,
                client_chat_id BIGINT,
                client_message_id BIGINT,
                request_chat_id BIGINT,
                request_message_id BIGINT,
                request_text TEXT,
                is_table_done BOOLEAN NOT NULL DEFAULT FALSE,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await con.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_exchange_request_links_table_req_id
            ON exchange_request_links(table_req_id)
            """
        )

    async def upsert_exchange_request_link(
        self,
        *,
        client_req_id: str,
        table_req_id: str,
        client_chat_id: int | None = None,
        client_message_id: int | None = None,
        request_chat_id: int | None = None,
        request_message_id: int | None = None,
        request_text: str | None = None,
        is_table_done: bool | None = None,
        status: str | None = None,
    ) -> None:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_exchange_request_links_table(con)
                await con.execute(
                    """
                    INSERT INTO exchange_request_links (
                        client_req_id,
                        table_req_id,
                        client_chat_id,
                        client_message_id,
                        request_chat_id,
                        request_message_id,
                        request_text,
                        is_table_done,
                        status,
                        updated_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7,
                        COALESCE($8, FALSE),
                        COALESCE($9, 'active'),
                        now()
                    )
                    ON CONFLICT (client_req_id) DO UPDATE SET
                        table_req_id = EXCLUDED.table_req_id,
                        client_chat_id = COALESCE(EXCLUDED.client_chat_id, exchange_request_links.client_chat_id),
                        client_message_id = COALESCE(EXCLUDED.client_message_id, exchange_request_links.client_message_id),
                        request_chat_id = COALESCE(EXCLUDED.request_chat_id, exchange_request_links.request_chat_id),
                        request_message_id = COALESCE(EXCLUDED.request_message_id, exchange_request_links.request_message_id),
                        request_text = COALESCE(EXCLUDED.request_text, exchange_request_links.request_text),
                        is_table_done = COALESCE($8, exchange_request_links.is_table_done),
                        status = COALESCE($9, exchange_request_links.status),
                        updated_at = now()
                    """,
                    str(client_req_id),
                    str(table_req_id),
                    int(client_chat_id) if client_chat_id is not None else None,
                    int(client_message_id) if client_message_id is not None else None,
                    int(request_chat_id) if request_chat_id is not None else None,
                    int(request_message_id) if request_message_id is not None else None,
                    request_text,
                    is_table_done,
                    status,
                )

    async def get_exchange_request_link(self, *, client_req_id: str) -> dict[str, Any] | None:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_exchange_request_links_table(con)
                row = await con.fetchrow(
                    "SELECT * FROM exchange_request_links WHERE client_req_id = $1 LIMIT 1",
                    str(client_req_id),
                )
                return dict(row) if row else None

    async def get_exchange_request_link_by_table_req_id(self, *, table_req_id: str) -> dict[str, Any] | None:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_exchange_request_links_table(con)
                row = await con.fetchrow(
                    "SELECT * FROM exchange_request_links WHERE table_req_id = $1 LIMIT 1",
                    str(table_req_id),
                )
                return dict(row) if row else None

    async def mark_exchange_request_table_done(self, *, table_req_id: str, is_table_done: bool = True) -> bool:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_exchange_request_links_table(con)
                res = await con.execute(
                    """
                    UPDATE exchange_request_links
                    SET is_table_done = $2,
                        updated_at = now()
                    WHERE table_req_id = $1
                    """,
                    str(table_req_id),
                    bool(is_table_done),
                )
                return not res.endswith(" 0")

    async def set_exchange_request_status(self, *, client_req_id: str, status: str) -> bool:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_exchange_request_links_table(con)
                res = await con.execute(
                    """
                    UPDATE exchange_request_links
                    SET status = $2,
                        updated_at = now()
                    WHERE client_req_id = $1
                    """,
                    str(client_req_id),
                    str(status),
                )
                return not res.endswith(" 0")
