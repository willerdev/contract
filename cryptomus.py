"""
Cryptomus API client: invoices (payments) and payouts.
Auth: merchant header + sign = MD5(base64(body) + API_KEY).
Docs: https://doc.cryptomus.com/
"""
import os
import json
import base64
import hashlib
import requests

CRYPTOMUS_API_BASE = "https://api.cryptomus.com"


def _get_merchant_id():
    return (os.environ.get("CRYPTOMUS_MERCHANT_ID") or "").strip()


def _get_webhook_base():
    return (os.environ.get("CRYPTOMUS_WEBHOOK_BASE") or "").strip().rstrip("/")


def is_configured():
    """True if Cryptomus payment (invoice) is configured."""
    return bool(
        _get_merchant_id()
        and os.environ.get("CRYPTOMUS_PAYMENT_API_KEY")
        and _get_webhook_base()
    )


def is_payout_configured():
    """True if Cryptomus payout is configured."""
    return bool(
        _get_merchant_id()
        and os.environ.get("CRYPTOMUS_PAYOUT_API_KEY")
        and _get_webhook_base()
    )


def _sign_body(body: str, api_key: str) -> str:
    """sign = MD5(base64(body) + api_key). Body is JSON string."""
    encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
    return hashlib.md5((encoded + api_key).encode("utf-8")).hexdigest()


def _request(method: str, path: str, data: dict, api_key: str):
    merchant = _get_merchant_id()
    if not merchant or not api_key:
        return None, "Cryptomus not configured"
    body = json.dumps(data) if data else ""
    sign = _sign_body(body, api_key)
    url = f"{CRYPTOMUS_API_BASE}{path}"
    headers = {
        "merchant": merchant,
        "sign": sign,
        "Content-Type": "application/json",
    }
    try:
        if method == "POST":
            r = requests.post(url, headers=headers, data=body, timeout=30)
        else:
            r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        out = r.json()
        if out.get("state") != 0:
            return None, out.get("message") or out.get("errors") or "Cryptomus error"
        return out.get("result"), None
    except requests.exceptions.RequestException as e:
        return None, str(e)
    except (ValueError, KeyError) as e:
        return None, str(e)


def create_invoice(
    amount: str,
    order_id: str,
    url_callback: str,
    currency: str = "USD",
    url_success: str = None,
    url_return: str = None,
    lifetime: int = 3600,
    **kwargs,
):
    """
    POST /v1/payment. Returns (result_dict, error_msg).
    result has: url, uuid, order_id, payment_status, etc.
    """
    api_key = (os.environ.get("CRYPTOMUS_PAYMENT_API_KEY") or "").strip()
    if not api_key:
        return None, "CRYPTOMUS_PAYMENT_API_KEY not set"
    data = {
        "amount": str(amount),
        "currency": currency,
        "order_id": order_id,
        "url_callback": url_callback,
        "lifetime": lifetime,
        **kwargs,
    }
    if url_success:
        data["url_success"] = url_success
    if url_return:
        data["url_return"] = url_return
    return _request("POST", "/v1/payment", data, api_key)


def create_payout(
    amount: str,
    currency: str,
    network: str,
    address: str,
    order_id: str,
    url_callback: str,
    is_subtract: bool = True,
    **kwargs,
):
    """
    POST /v1/payout. Returns (result_dict, error_msg).
    result has: uuid, status, is_final, etc.
    """
    api_key = (os.environ.get("CRYPTOMUS_PAYOUT_API_KEY") or "").strip()
    if not api_key:
        return None, "CRYPTOMUS_PAYOUT_API_KEY not set"
    data = {
        "amount": str(amount),
        "currency": currency,
        "network": network,
        "address": address,
        "order_id": order_id,
        "url_callback": url_callback,
        "is_subtract": "1" if is_subtract else "0",
        **kwargs,
    }
    return _request("POST", "/v1/payout", data, api_key)


def verify_webhook_signature(body_dict: dict, api_key: str) -> bool:
    """
    Verify Cryptomus webhook sign. body_dict is the full JSON body.
    Remove 'sign' from copy, then MD5(base64(json) + api_key) == received sign.
    """
    if not body_dict or not api_key:
        return False
    received = body_dict.get("sign")
    if not received:
        return False
    copy = dict(body_dict)
    del copy["sign"]
    # JSON without sign; match Cryptomus encoding (no extra spaces)
    body_str = json.dumps(copy, separators=(",", ":"), ensure_ascii=False)
    expected = _sign_body(body_str, api_key)
    return expected == received
