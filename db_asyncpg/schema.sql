-- Таблица клиентов (Telegram-чаты)
CREATE TABLE IF NOT EXISTS clients (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    city TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Счета клиента (валюты)
CREATE TABLE IF NOT EXISTS client_accounts (
    id BIGSERIAL PRIMARY KEY,
    client_id BIGINT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    currency_code TEXT NOT NULL, -- хранить в UPPER на уровне приложения
    precision SMALLINT NOT NULL CHECK (precision BETWEEN 0 AND 8),
    balance NUMERIC(38,8) NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deactivated_at TIMESTAMPTZ,
    UNIQUE (client_id, currency_code)
);
CREATE INDEX IF NOT EXISTS ix_client_accounts_client ON client_accounts(client_id);


-- Группы/категории операций (необязательно использовать)
CREATE TABLE IF NOT EXISTS txn_groups (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);


-- Исполнители/акторы (кто провёл операцию) — опционально
CREATE TABLE IF NOT EXISTS actors (
    id BIGSERIAL PRIMARY KEY,
    display_name TEXT NOT NULL,
    external_ref TEXT
);

CREATE TABLE IF NOT EXISTS managers (
    user_id BIGINT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    added_at timestamptz NOT NULL DEFAULT now()
);

-- Транзакции по счетам клиента
CREATE TABLE IF NOT EXISTS transactions (
    id BIGSERIAL PRIMARY KEY,
    client_id BIGINT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    account_id BIGINT NOT NULL REFERENCES client_accounts(id) ON DELETE CASCADE,
    txn_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    amount NUMERIC(38,8) NOT NULL, -- знаковая величина
    balance_after NUMERIC(38,8) NOT NULL, -- остаток после операции
    group_id INT REFERENCES txn_groups(id),
    actor_id BIGINT REFERENCES actors(id),
    comment TEXT,
    source TEXT,
    idempotency_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- Индексы для выписок и агрегатов
CREATE INDEX IF NOT EXISTS ix_tx_account_time ON transactions(account_id, txn_at, id);
CREATE INDEX IF NOT EXISTS ix_tx_client_time ON transactions(client_id, txn_at, id);


-- Идемпотентность на уровне клиента
CREATE UNIQUE INDEX IF NOT EXISTS uq_tx_client_idem
ON transactions(client_id, idempotency_key)
WHERE idempotency_key IS NOT NULL;