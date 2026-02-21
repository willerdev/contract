-- Run this in Neon SQL Editor. Run each ALTER once; ignore "column already exists" if re-running.

-- Contracts: payment verification
ALTER TABLE contracts ADD COLUMN payment_wallet VARCHAR(255);
ALTER TABLE contracts ADD COLUMN payment_tx_id VARCHAR(255);

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

-- ========== FIX: Update existing contracts to "pending" (if they were created as "active") ==========
-- Run this if you have old contracts that were created before the code update:
-- UPDATE contracts SET status = 'pending' WHERE status = 'active';
-- (This will set all active contracts to pending, requiring system approval again)
