from __future__ import annotations

from db_asyncpg.pool import get_pool


class SettingsRepo:
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
