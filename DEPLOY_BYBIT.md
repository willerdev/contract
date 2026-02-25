# Deploy and use Bybit with Render

## 1. Deploy the app

Push to your repo so Render redeploys:

```bash
cd /path/to/airmouse
git add .
git commit -m "Your message"
git push origin main
```

Wait for the deploy to finish in the [Render Dashboard](https://dashboard.render.com).

---

## 2. Get Render’s outbound IP (for Bybit)

After the app is live, open this URL in your browser:

**https://contract-31az.onrender.com/outbound-ip**

You’ll see JSON like:

```json
{
  "outbound_ip": "123.45.67.89",
  "note": "Whitelist this IP in Bybit API key settings (API Management → IP restriction)."
}
```

The `outbound_ip` value is the IP your Render app uses when it calls Bybit.

---

## 3. Whitelist that IP in Bybit

1. Log in at [Bybit](https://www.bybit.com) → **API Management**.
2. Open your API key (or create one with **Withdrawal** permission).
3. Under **IP restriction**, add the IP from step 2 (e.g. `123.45.67.89`).
4. Save.

If your Render plan uses **multiple outbound IPs** (e.g. a range), check the **Outbound** tab for your service in the Render Dashboard and add each IP (or the CIDR range if Bybit supports it).

---

## 4. Add Bybit keys on Render

1. In [Render Dashboard](https://dashboard.render.com) → your **Web Service**.
2. Go to **Environment**.
3. Add:
   - `BYBIT_API_KEY` = your Bybit API key  
   - `BYBIT_API_SECRET` = your Bybit API secret  
4. Save. Render will redeploy with the new env vars.

---

## 5. Check that Bybit works

**From your computer (uses your local IP):**

```bash
cd /path/to/airmouse
python3 check_bybit_balance.py
```

This uses the API keys from your local `.env`. Bybit will only accept it if your **current public IP** is whitelisted for that key. To test the **server** side, use the steps below.

**From the server (Render’s IP):**

- The app uses Bybit when a user withdraws (during the withdraw window). If Render’s IP is whitelisted and `BYBIT_API_KEY` / `BYBIT_API_SECRET` are set on Render, withdrawals will go through Bybit.
- There is no “run balance check on Render” script by default; the balance check script is for local use. To confirm the server can reach Bybit, trigger a small test withdrawal (or check logs after a real one).

---

## Summary

| Step | Action |
|------|--------|
| 1 | Push to `main` and wait for Render deploy |
| 2 | Open **https://contract-31az.onrender.com/outbound-ip** and copy `outbound_ip` |
| 3 | In Bybit → API Management → your key → add that IP under IP restriction |
| 4 | In Render → Environment → add `BYBIT_API_KEY` and `BYBIT_API_SECRET` |
| 5 | Run `python3 check_bybit_balance.py` locally to test (whitelist your home IP if needed) |
