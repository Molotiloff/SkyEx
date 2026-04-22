from __future__ import annotations

from typing import Any

from db_asyncpg.pool import get_pool


class LiveMessagesRepo:
    async def _ensure_live_messages_table(self, con) -> None:
        await con.execute(
            """
            CREATE TABLE IF NOT EXISTS live_messages (
                chat_id BIGINT NOT NULL,
                message_key TEXT NOT NULL,
                message_id BIGINT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (chat_id, message_key)
            )
            """
        )
        await con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_live_messages_message_key
            ON live_messages(message_key)
            """
        )

    async def upsert_live_message(
        self,
        *,
        chat_id: int,
        message_key: str,
        message_id: int,
    ) -> None:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_live_messages_table(con)
                await con.execute(
                    """
                    INSERT INTO live_messages (
                        chat_id,
                        message_key,
                        message_id,
                        updated_at
                    )
                    VALUES ($1, $2, $3, now())
                    ON CONFLICT (chat_id, message_key) DO UPDATE SET
                        message_id = EXCLUDED.message_id,
                        updated_at = now()
                    """,
                    int(chat_id),
                    message_key.strip(),
                    int(message_id),
                )

    async def get_live_message(
        self,
        *,
        chat_id: int,
        message_key: str,
    ) -> dict[str, Any] | None:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_live_messages_table(con)
                row = await con.fetchrow(
                    """
                    SELECT chat_id, message_key, message_id, updated_at
                    FROM live_messages
                    WHERE chat_id = $1
                      AND message_key = $2
                    LIMIT 1
                    """,
                    int(chat_id),
                    message_key.strip(),
                )
                return dict(row) if row else None

    async def delete_live_message(
        self,
        *,
        chat_id: int,
        message_key: str,
    ) -> bool:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_live_messages_table(con)
                res = await con.execute(
                    """
                    DELETE FROM live_messages
                    WHERE chat_id = $1
                      AND message_key = $2
                    """,
                    int(chat_id),
                    message_key.strip(),
                )
                return res.endswith(" 1")

    async def list_live_messages(
        self,
        *,
        message_key: str | None = None,
    ) -> list[dict[str, Any]]:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._ensure_live_messages_table(con)

                if message_key:
                    rows = await con.fetch(
                        """
                        SELECT chat_id, message_key, message_id, updated_at
                        FROM live_messages
                        WHERE message_key = $1
                        ORDER BY updated_at DESC, chat_id ASC
                        """,
                        message_key.strip(),
                    )
                else:
                    rows = await con.fetch(
                        """
                        SELECT chat_id, message_key, message_id, updated_at
                        FROM live_messages
                        ORDER BY updated_at DESC, chat_id ASC
                        """
                    )

                return [dict(r) for r in rows]
