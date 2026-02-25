"""
Bybit V5 API client for automatic withdrawals.
Withdrawal address must be whitelisted in Bybit address book first: https://www.bybit.com/user/assets/money-address
Docs: https://bybit-exchange.github.io/docs/v5/asset/withdraw
"""
import os
import json
import time
import hmac
import hashlib
import requests

BYBIT_BASE_URL = (os.environ.get("BYBIT_BASE_URL") or "https://api.bybit.com").strip().rstrip("/")
RECV_WINDOW = "5000"


def _api_key():
    return (os.environ.get("BYBIT_API_KEY") or "").strip()


def _api_secret():
    return (os.environ.get("BYBIT_API_SECRET") or "").strip()


def _withdraw_coin():
    return (os.environ.get("BYBIT_WITHDRAW_COIN") or "USDT").strip().upper()


def _withdraw_chain():
    return (os.environ.get("BYBIT_WITHDRAW_CHAIN") or "TRON").strip().upper()


def is_configured():
    return bool(_api_key() and _api_secret())


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _server_outbound_ip() -> str:
    """Get this server's outbound IP (so user can whitelist the exact IP Bybit sees)."""
    try:
        r = requests.get("https://api.ipify.org?format=json", timeout=3)
        if r.ok:
            return (r.json() or {}).get("ip") or "unknown"
    except Exception:
        pass
    return "unknown"


def create_withdraw(address: str, amount: str, request_id: str = None) -> tuple:
    """
    Submit on-chain withdrawal. Returns (withdrawal_id, None) on success or (None, error_msg).
    Address must already be in your Bybit address book.
    """
    api_key = _api_key()
    secret = _api_secret()
    if not api_key or not secret:
        return None, "BYBIT_API_KEY and BYBIT_API_SECRET not set"
    coin = _withdraw_coin()
    chain = _withdraw_chain()
    timestamp = str(int(time.time() * 1000))
    body = {
        "coin": coin,
        "chain": chain,
        "address": address.strip(),
        "amount": str(amount),
        "timestamp": int(timestamp),
        "forceChain": 1,
        "accountType": "FUND",
    }
    if request_id:
        body["requestId"] = request_id[:32]
    body_str = json.dumps(body, separators=(",", ":"))
    sign_payload = timestamp + api_key + RECV_WINDOW + body_str
    signature = _sign(secret, sign_payload)
    url = f"{BYBIT_BASE_URL}/v5/asset/withdraw/create"
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-SIGN": signature,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(url, headers=headers, data=body_str, timeout=30)
        raw = (r.text or "").strip()
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            server_ip = _server_outbound_ip()
            snippet = (raw[:120] + "…") if len(raw) > 120 else (raw or "(empty)")
            return None, (
                f"Bybit returned non-JSON (status {r.status_code}). "
                f"Add this IP in Bybit API key IP restriction: {server_ip}. "
                f"Response: {snippet}"
            )
        ret_code = data.get("retCode", -1)
        ret_msg = data.get("retMsg", "")
        if ret_code == 0:
            result = data.get("result") or {}
            return result.get("id"), None
        err = ret_msg or data.get("retExtInfo") or str(data)
        if "whitelist" in str(err).lower() or ("address" in str(err).lower() and "book" in str(err).lower()):
            return None, "Address not in Bybit address book. Add it at https://www.bybit.com/user/assets/money-address"
        if ret_code == 10010 or "unmatched ip" in str(err).lower() or "ip" in str(err).lower():
            server_ip = _server_outbound_ip()
            return None, f"{err} Add this IP in Bybit API key IP restriction: {server_ip}"
        return None, err
    except requests.RequestException as e:
        return None, str(e)
    except (ValueError, KeyError) as e:
        return None, str(e)
