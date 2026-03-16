-- Neon (PostgreSQL) migration: Messages (user support: write, inbox, outbox)
-- Run this in the Neon SQL Editor or via psql. Safe to run multiple times (CREATE TABLE IF NOT EXISTS).
-- from_admin = false: user sent; from_admin = true: admin/support reply. read_at set when user opens inbox message.

CREATE TABLE IF NOT EXISTS messages (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL,
  from_admin BOOLEAN NOT NULL DEFAULT false,
  subject VARCHAR(255),
  body TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  read_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages (user_id);
CREATE INDEX IF NOT EXISTS idx_messages_user_from_admin_read ON messages (user_id, from_admin, read_at);
