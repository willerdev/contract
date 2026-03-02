"""
Bybit V5 API client for automatic withdrawals.
V5 docs: https://bybit-exchange.github.io/docs/v5/intro | Withdraw: https://bybit-exchange.github.io/docs/v5/asset/withdraw
Withdrawal address must be whitelisted: https://www.bybit.com/user/assets/money-address
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


def get_withdrawable_amount(coin: str = None) -> tuple:
    """
    Get actual withdrawable amount (avoids 131001). Per Bybit FAQ: deposit risk can lock funds.
    Returns ({"withdrawableAmount", "availableBalance", "limitAmountUsd", "coin"}, None) or (None, error).
    Uses GET /v5/asset/withdraw/withdrawable-amount.
    """
    api_key = _api_key()
    secret = _api_secret()
    if not api_key or not secret:
        return None, "BYBIT_API_KEY and BYBIT_API_SECRET not set"
    c = (coin or _withdraw_coin()).strip().upper()
    query = f"coin={c}"
    ts_ms = _bybit_server_time_ms()
    timestamp = str(ts_ms)
    sign_payload = timestamp + api_key + RECV_WINDOW + query
    signature = _sign(secret, sign_payload)
    url = f"{BYBIT_BASE_URL}/v5/asset/withdraw/withdrawable-amount?{query}"
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-SIGN": signature,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json() if r.text else {}
        if data.get("retCode", -1) != 0:
            return None, data.get("retMsg") or data.get("retExtInfo") or str(data)
        result = data.get("result") or {}
        limit_usd = result.get("limitAmountUsd") or "0"
        wa = result.get("withdrawableAmount") or {}
        fund = wa.get("FUND") or {}
        out = {
            "withdrawableAmount": fund.get("withdrawableAmount") or "0",
            "availableBalance": fund.get("availableBalance") or "0",
            "limitAmountUsd": limit_usd,
            "coin": c,
        }
        return out, None
    except requests.RequestException as e:
        return None, str(e)
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        return None, str(e)


def get_funding_balance(coin: str = None) -> tuple:
    """
    Get Funding account balance. Returns (list of {coin, walletBalance, transferBalance}, None) or (None, error).
    If coin is None, uses BYBIT_WITHDRAW_COIN (default USDT).
    """
    api_key = _api_key()
    secret = _api_secret()
    if not api_key or not secret:
        return None, "BYBIT_API_KEY and BYBIT_API_SECRET not set"
    c = (coin or _withdraw_coin()).strip().upper()
    query = f"accountType=FUND&coin={c}"
    ts_ms = _bybit_server_time_ms()
    timestamp = str(ts_ms)
    sign_payload = timestamp + api_key + RECV_WINDOW + query
    signature = _sign(secret, sign_payload)
    url = f"{BYBIT_BASE_URL}/v5/asset/transfer/query-account-coins-balance?{query}"
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-SIGN": signature,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json() if r.text else {}
        if data.get("retCode", -1) != 0:
            return None, data.get("retMsg") or data.get("retExtInfo") or str(data)
        result = data.get("result") or {}
        balance_list = result.get("balance") or []
        return balance_list, None
    except requests.RequestException as e:
        return None, str(e)
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        return None, str(e)


def _bybit_server_time_ms() -> int:
    """Get Bybit server time in ms (avoids clock skew). V5: GET /v5/market/time."""
    try:
        r = requests.get(f"{BYBIT_BASE_URL}/v5/market/time", timeout=5)
        if r.ok:
            data = r.json() or {}
            t = data.get("time")
            if t is not None:
                return int(t)
            result = data.get("result") or {}
            sec = result.get("timeSecond")
            if sec is not None:
                return int(sec) * 1000
    except Exception:
        pass
    return int(time.time() * 1000)


def create_withdraw(address: str, amount: str, request_id: str = None) -> tuple:
    """
    Submit on-chain withdrawal. Returns (withdrawal_id, None) on success or (None, error_msg).
    Address must already be in your Bybit address book.
    Checks withdrawable amount first to avoid 131001 (deposit risk locks part of balance).
    """
    api_key = _api_key()
    secret = _api_secret()
    if not api_key or not secret:
        return None, "BYBIT_API_KEY and BYBIT_API_SECRET not set"
    coin = _withdraw_coin()
    chain = _withdraw_chain()
    withdrawable, w_err = get_withdrawable_amount(coin)
    if withdrawable and not w_err:
        try:
            max_val = float(withdrawable.get("withdrawableAmount") or 0)
            if max_val > 0 and float(amount) > max_val:
                return None, (
                    f"Amount exceeds Bybit withdrawable amount ({max_val} {coin}). "
                    "Deposit risk may lock part of your balance; try a smaller amount or wait. "
                    "See: https://bybit-exchange.github.io/docs/faq#why-is-my-balance-sufficient-but-withdrawal-or-transfer-requests-report-insufficient-balance"
                )
        except (TypeError, ValueError):
            pass
    ts_ms = _bybit_server_time_ms()
    timestamp = str(ts_ms)
    body = {
        "accountType": "FUND",
        "address": address.strip(),
        "amount": str(amount),
        "chain": chain,
        "coin": coin,
        "forceChain": 1,
        "feeType": 1,
        "timestamp": ts_ms,
    }
    if request_id:
        body["requestId"] = request_id[:32]
    body_str = json.dumps(body, sort_keys=True, separators=(",", ":"))
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
        ret_ext = data.get("retExtInfo")
        if ret_code == 0:
            result = data.get("result") or {}
            return result.get("id"), None
        err = ret_msg or (str(ret_ext) if ret_ext is not None else "") or str(data)
        if "whitelist" in str(err).lower() or ("address" in str(err).lower() and "book" in str(err).lower()):
            return None, "Address not in Bybit address book. Add it at https://www.bybit.com/user/assets/money-address"
        if ret_code == 10010 or "unmatched ip" in str(err).lower() or "ip" in str(err).lower():
            server_ip = _server_outbound_ip()
            return None, f"{err} Add this IP in Bybit API key IP restriction: {server_ip}"
        if ret_code == 131001:
            return None, (
                f"Insufficient balance (131001). "
                "Amount must be ≤ withdrawable amount; fee is deducted from the amount (feeType=1). "
                "Wait for deposit risk to clear or try a smaller amount. "
                "See: https://bybit-exchange.github.io/docs/faq#why-is-my-balance-sufficient-but-withdrawal-or-transfer-requests-report-insufficient-balance"
            )
        if ret_code != -1:
            err = f"retCode={ret_code}, {err}"
        if ret_ext:
            err = f"{err} (retExtInfo: {ret_ext})"
        return None, err
    except requests.RequestException as e:
        return None, str(e)
    except (ValueError, KeyError) as e:
        return None, str(e)
