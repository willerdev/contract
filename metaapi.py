"""
MetaAPI client: add MT4/MT5 accounts and fetch account info (balance).
Docs: https://metaapi.cloud/docs/provisioning/ and https://metaapi.cloud/docs/client/
"""
import os
import secrets
import requests

METAAPI_PROVISIONING_URL = (
    os.environ.get("METAAPI_PROVISIONING_URL") or "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai"
).strip().rstrip("/")
METAAPI_CLIENT_URL = (
    os.environ.get("METAAPI_CLIENT_URL") or "https://mt-client-api-v1.new-york.agiliumtrade.ai"
).strip().rstrip("/")


def _token():
    return (os.environ.get("METAAPI_TOKEN") or "").strip()


def is_configured():
    return bool(_token())


def add_account(login: str, password: str, server: str, name: str, platform: str = "mt5"):
    """
    Add a trading account to MetaAPI. Returns (account_id, None) on success or (None, error_msg).
    """
    token = _token()
    if not token:
        return None, "METAAPI_TOKEN not set"
    transaction_id = secrets.token_hex(16)
    url = f"{METAAPI_PROVISIONING_URL}/users/current/accounts"
    headers = {
        "Content-Type": "application/json",
        "auth-token": token,
        "transaction-id": transaction_id,
    }
    payload = {
        "login": str(login).strip(),
        "password": password,
        "server": server.strip(),
        "name": (name or login).strip()[:128],
        "platform": "mt5" if (platform or "").strip().lower() == "mt5" else "mt4",
        "magic": 0,
        "manualTrades": True,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code == 201:
            data = r.json()
            return data.get("id"), None
        if r.status_code == 202:
            return None, "Account is being created. Try again in a minute."
        err = r.json() if r.text else {}
        msg = err.get("message") or err.get("error") or r.text or str(r.status_code)
        return None, msg
    except requests.RequestException as e:
        return None, str(e)


def get_account_information(account_id: str):
    """
    Get account info (balance, equity, etc.) for a MetaAPI account. Returns (info_dict, None) or (None, error_msg).
    """
    token = _token()
    if not token:
        return None, "METAAPI_TOKEN not set"
    url = f"{METAAPI_CLIENT_URL}/users/current/accounts/{account_id}/account-information"
    headers = {"Accept": "application/json", "auth-token": token}
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.json(), None
        err = r.json() if r.text else {}
        msg = err.get("message") or err.get("error") or r.text or str(r.status_code)
        return None, msg
    except requests.RequestException as e:
        return None, str(e)
