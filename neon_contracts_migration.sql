-- Run this in Neon SQL Editor. Run each ALTER once; ignore "column already exists" if re-running.

-- Contracts: payment verification
ALTER TABLE contracts ADD COLUMN payment_wallet VARCHAR(255);
ALTER TABLE contracts ADD COLUMN payment_tx_id VARCHAR(255);

-- Contracts: duration and refund
ALTER TABLE contracts ADD COLUMN duration_days INTEGER;
ALTER TABLE contracts ADD COLUMN refunded_at TIMESTAMP;

-- Users: withdrawable amount set by system (users cannot withdraw contract principal; only this amount)
ALTER TABLE users ADD COLUMN available_for_withdraw DOUBLE PRECISION DEFAULT 0;

-- ========== SYSTEM: Verify payment and activate a contract ==========
-- UPDATE contracts SET status = 'active' WHERE id = <contract_id>;
-- Example: UPDATE contracts SET status = 'active' WHERE id = 5;

-- ========== SYSTEM: List pending contracts ==========
-- SELECT id, user_id, amount, payment_wallet, payment_tx_id, status, start_date
-- FROM contracts WHERE status = 'pending' ORDER BY id DESC;

-- ========== SYSTEM: Set user's available amount for withdrawal ==========
-- UPDATE users SET available_for_withdraw = <amount> WHERE id = <user_id>;
-- Example: UPDATE users SET available_for_withdraw = 100.50 WHERE id = 1;

-- ========== Run sessions (22h run, earnings every 10 min to withdrawables) ==========
CREATE TABLE IF NOT EXISTS run_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    contract_id INTEGER NOT NULL,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    last_heartbeat_at TIMESTAMP,
    earnings_added DOUBLE PRECISION DEFAULT 0,
    last_earnings_saved_at TIMESTAMP
);

-- ========== Run earnings (one row per 10-min chunk) ==========
CREATE TABLE IF NOT EXISTS run_earnings (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES run_sessions(id),
    amount DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- If run_sessions already existed without last_earnings_saved_at, add it (ignore if exists):
ALTER TABLE run_sessions ADD COLUMN last_earnings_saved_at TIMESTAMP;

-- ========== FIX: Update existing contracts to "pending" (if they were created as "active") ==========
-- Run this if you have old contracts that were created before the code update:
-- UPDATE contracts SET status = 'pending' WHERE status = 'active';
-- (This will set all active contracts to pending, requiring system approval again)
