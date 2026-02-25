# Backend environment variables

Set these on the server where `main2.py` runs (e.g. Render dashboard).

## Database

- `DATABASE_URL` — PostgreSQL connection string (e.g. Neon).

## Withdrawals

### Cryptomus (optional)

If set, withdrawals are sent via Cryptomus payouts.

- `CRYPTOMUS_MERCHANT_ID`, `CRYPTOMUS_PAYMENT_API_KEY`, `CRYPTOMUS_PAYOUT_API_KEY`
- `CRYPTOMUS_WEBHOOK_BASE` — Base URL for payout webhooks (e.g. `https://your-app.onrender.com`)
- `CRYPTOMUS_PAYOUT_CURRENCY` (default `USDT`), `CRYPTOMUS_PAYOUT_NETWORK` (default `TRON`)

### Bybit (optional)

If Cryptomus is **not** configured and Bybit is, withdrawals are sent automatically via Bybit Asset API.

- `BYBIT_API_KEY` — Bybit API key (with withdraw permission).
- `BYBIT_API_SECRET` — Bybit API secret.
- `BYBIT_BASE_URL` — Optional. Default `https://api.bybit.com`; use `https://api-testnet.bybit.com` for testnet.
- `BYBIT_WITHDRAW_COIN` — Coin to withdraw (default `USDT`).
- `BYBIT_WITHDRAW_CHAIN` — Chain, e.g. `TRON`, `ETH` (default `TRON`).

**Important:** Withdrawal addresses must be **whitelisted in your Bybit address book** before users can withdraw to them. Add addresses at: https://www.bybit.com/user/assets/money-address  
If a user’s wallet is not whitelisted, the API will return an error and the withdrawal will fail (user balance is refunded).

### Bybit IP whitelist

Bybit can restrict API keys to specific IPs. The IP that matters is **your server's outbound IP** (the machine that runs `main2.py`), **not** your app users' IPs. There is no way to send a "universal" or custom IP in the API call—Bybit always sees the TCP source IP of the request.

- **Find your server's outbound IP:** After deploy, open `https://your-app.onrender.com/outbound-ip` (or your backend URL + `/outbound-ip`). The response shows the IP Bybit sees. Whitelist that IP in Bybit: **Bybit → API Management → your key → IP restriction**.
- **Render:** Outbound IPs may be **region-specific ranges** (see Dashboard → your service → **Outbound** tab). If Bybit accepts CIDR, whitelist that range; otherwise you may need a **static egress IP** (e.g. [QuotaGuard Static](https://render.com/docs/quotaguard) on Render) or a VPS with a fixed IP so a single IP can be whitelisted.
- **Multiple IPs:** If your host uses several IPs (e.g. multiple regions or proxies), whitelist each one in Bybit, or use a single static egress (proxy/VPS) so only one IP is used for API calls.

## Telegram

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`

## MetaAPI (trading accounts)

- `METAAPI_TOKEN`

## Precedence

- **Withdrawals:** Cryptomus is used first if payout is configured; otherwise Bybit is used if configured; otherwise withdrawals are stored as “pending” (no automatic send).
