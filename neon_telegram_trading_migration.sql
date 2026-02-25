-- Neon (PostgreSQL) migration: Telegram, Trading accounts, Account Management
-- Run this in the Neon SQL Editor or via psql. Safe to run multiple times (idempotent where possible).

-- 1. New columns on users
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'telegram_chat_id') THEN
    ALTER TABLE users ADD COLUMN telegram_chat_id VARCHAR(32);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'telegram_username') THEN
    ALTER TABLE users ADD COLUMN telegram_username VARCHAR(128);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'account_management_paid_at') THEN
    ALTER TABLE users ADD COLUMN account_management_paid_at TIMESTAMP;
  END IF;
END $$;

-- 2. telegram_link_tokens (one-time link Telegram to user)
CREATE TABLE IF NOT EXISTS telegram_link_tokens (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL,
  token VARCHAR(64) UNIQUE NOT NULL,
  expires_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. trading_accounts (MetaAPI MT4/MT5 accounts)
CREATE TABLE IF NOT EXISTS trading_accounts (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL,
  metaapi_account_id VARCHAR(64) NOT NULL,
  login VARCHAR(32) NOT NULL,
  server VARCHAR(128) NOT NULL,
  label VARCHAR(128),
  platform VARCHAR(8) DEFAULT 'mt5',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. account_management_payments ($50 one-time for Telegram + Trading access)
CREATE TABLE IF NOT EXISTS account_management_payments (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL,
  amount DOUBLE PRECISION NOT NULL,
  payment_wallet VARCHAR(255),
  payment_tx_id VARCHAR(255),
  status VARCHAR(32) DEFAULT 'pending',
  verified_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
