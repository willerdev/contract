#!/usr/bin/env python3
"""
Check Bybit API access and Funding account balance (USDT).
Run from project root with .env set: python check_bybit_balance.py
"""
import os
import time
import hmac
import hashlib
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BYBIT_BASE_URL = (os.environ.get("BYBIT_BASE_URL") or "https://api.bybit.com").strip().rstrip("/")
RECV_WINDOW = "5000"


def main():
    api_key = (os.environ.get("BYBIT_API_KEY") or "").strip()
    api_secret = (os.environ.get("BYBIT_API_SECRET") or "").strip()

    if not api_key or not api_secret:
        print("Missing BYBIT_API_KEY or BYBIT_API_SECRET in .env")
        return 1

    # Funding account balance (same account type used for withdrawals)
    # GET /v5/asset/transfer/query-account-coins-balance?accountType=FUND&coin=USDT
    query = "accountType=FUND&coin=USDT"
    timestamp = str(int(time.time() * 1000))
    sign_payload = timestamp + api_key + RECV_WINDOW + query
    signature = hmac.new(api_secret.encode("utf-8"), sign_payload.encode("utf-8"), hashlib.sha256).hexdigest()

    url = f"{BYBIT_BASE_URL}/v5/asset/transfer/query-account-coins-balance?{query}"
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-SIGN": signature,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
    }

    print("Bybit balance check (Funding account, USDT)")
    print(f"URL: {url}")
    print()

    try:
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json() if r.text else {}
        ret_code = data.get("retCode", -1)
        ret_msg = data.get("retMsg", "")

        if ret_code != 0:
            print(f"API error: retCode={ret_code}, retMsg={ret_msg}")
            if data.get("retExtInfo"):
                print(f"retExtInfo: {data['retExtInfo']}")
            return 1

        result = data.get("result") or {}
        account_type = result.get("accountType", "?")
        balance_list = result.get("balance") or []

        print("API OK – Funding account balance:")
        print(f"  accountType: {account_type}")
        if not balance_list:
            print("  (no coins or zero balance)")
        for b in balance_list:
            coin = b.get("coin", "?")
            wallet = b.get("walletBalance", "0")
            transfer = b.get("transferBalance", "0")
            print(f"  {coin}: walletBalance={wallet}, transferBalance={transfer}")
        print()
        print("Bybit API is working. You can use this key for withdrawals (after IP whitelist if required).")
        return 0

    except requests.RequestException as e:
        print(f"Request failed: {e}")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
