# Refund feature – deploy steps

## 1. Run SQL on Neon

In the [Neon Console](https://console.neon.tech) SQL Editor for your project, run the migration:

**File:** `neon_refund_requests_migration.sql`

Or paste and run:

```sql
-- refund_requests: user requests refund for a contract; status: pending | approved | rejected | paid
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
CREATE INDEX IF NOT EXISTS idx_refund_requests_user_id ON refund_requests (user_id);
CREATE INDEX IF NOT EXISTS idx_refund_requests_status ON refund_requests (status);
```

## 2. Deploy backend and CLI

- **Backend (e.g. Render):** Push to your repo; Render will redeploy and use the new `refund_requests` table (Postgres auto-migration in `database.py` also creates the table if it’s missing).
- **CLI:** No env changes. Users get the new “8. Refund” menu after you deploy the backend.

## 3. Optional: admin flow

To approve/reject or mark refunds as paid, use the DB or add an admin API that updates `refund_requests.status` and `admin_notes`.
