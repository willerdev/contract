-- Neon (PostgreSQL) migration: Refund requests for bought contracts
-- Run this in the Neon SQL Editor or via psql. Safe to run multiple times (CREATE TABLE IF NOT EXISTS).

-- refund_requests: user requests refund for a contract; admin can approve/reject; status tracked until paid
CREATE TABLE IF NOT EXISTS refund_requests (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL,
  contract_id INTEGER NOT NULL,
  reason TEXT,
  wallet VARCHAR(255) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  admin_notes TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Optional: index for listing by user and by status
CREATE INDEX IF NOT EXISTS idx_refund_requests_user_id ON refund_requests (user_id);
CREATE INDEX IF NOT EXISTS idx_refund_requests_status ON refund_requests (status);
