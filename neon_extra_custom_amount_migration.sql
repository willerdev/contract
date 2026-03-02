-- Neon: add per-user custom contract amount (Extra menu)
-- Run in Neon SQL Editor. Safe to run multiple times.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'custom_contract_amount') THEN
    ALTER TABLE users ADD COLUMN custom_contract_amount DOUBLE PRECISION;
  END IF;
END $$;
