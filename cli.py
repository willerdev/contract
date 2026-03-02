import sys
import time
import threading
import requests
import getpass
import json
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_URL = os.environ.get("BASE_URL", "https://contract-31az.onrender.com")
TOKEN_FILE = "token.txt"


# Timeout for server check. Render free tier can take 30–60s to wake from spin-down.
SERVER_CHECK_TIMEOUT = int(os.environ.get("CLI_SERVER_TIMEOUT", "75"))

# Cached: is Trading accounts (MetaAPI) available on the server? None = not yet checked; True/False = cached. Cleared on logout.
_trading_available = None

APP_LOGO = r"""
   ____            _             _   
  / ___|___  _ __ | |_ _ __ __ _| |_ 
 | |   / _ \| '_ \| __| '__/ _` | __|
 | |__| (_) | | | | |_| | | (_| | |_ 
  \____\___/|_| |_|\__|_|  \__,_|\__|
"""


def clear_screen():
    if os.name == "nt":
        os.system("cls")
    else:
        os.system("clear")


def print_header():
    print(APP_LOGO)


def _loading(callback, message="Loading"):
    """Run callback and show a spinner until it returns."""
    done = threading.Event()
    result = []

    def spinner():
        chars = "|/-\\"
        i = 0
        while not done.is_set():
            print("\r  " + message + " " + chars[i % 4], end="", flush=True)
            i += 1
            done.wait(0.12)
        print("\r" + " " * (len(message) + 4) + "\r", end="", flush=True)

    t = threading.Thread(target=spinner, daemon=True)
    t.start()
    try:
        result.append(callback())
    finally:
        done.set()
        time.sleep(0.05)
    return result[0] if result else None


def _check_server():
    """Raise a clear error if the backend server is not reachable."""
    try:
        requests.get(f"{BASE_URL}/", timeout=SERVER_CHECK_TIMEOUT)
    except requests.exceptions.ConnectionError:
        raise SystemExit(
            f"Cannot reach server at {BASE_URL}. Connection refused.\n"
            "Start the backend server first (e.g. in another terminal), then run this CLI again."
        )
    except requests.exceptions.Timeout:
        raise SystemExit(
            f"Server at {BASE_URL} did not respond in {SERVER_CHECK_TIMEOUT}s.\n"
            "If using Render free tier, the service may be waking up—try again in a minute."
        )


def save_token(token):
    with open(TOKEN_FILE, "w") as f:
        f.write(token)


def load_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r") as f:
        return f.read().strip()


def logout():
    global _trading_available
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    _trading_available = None
    print("✅ Logged out")


def _parse_response(res):
    """Return (data dict or None, error message or None). Handles empty/non-JSON body. Data can be dict or list."""
    if not res.text or not res.text.strip():
        return None, f"Server returned empty response (status {res.status_code})"
    try:
        data = res.json()
        if res.status_code >= 400:
            detail = data.get("detail")
            if isinstance(detail, dict) and detail.get("code") == "telegram_trading_requirement":
                return data, "telegram_trading_requirement"  # caller will show requirement
            if isinstance(detail, dict):
                return data, detail.get("message") or str(detail)
            return data, str(detail) if detail is not None else f"Error {res.status_code}"
        return data, None
    except json.JSONDecodeError:
        return None, f"Server response not JSON (status {res.status_code}): {res.text[:200]}"


def _normalize_contract_list(raw):
    """Take contract_list from API (list of dicts, or list of ids) and return list of dicts with id, amount, status."""
    if not raw or not isinstance(raw, list):
        return []
    out = []
    for c in raw:
        if isinstance(c, dict):
            cid = c.get("id") if c.get("id") is not None else c.get("contractId") or c.get("contract_id")
            if cid is not None:
                out.append({"id": cid, "amount": c.get("amount", 0), "status": c.get("status", "?")})
        elif isinstance(c, (int, float, str)) and c != "":
            out.append({"id": int(c) if isinstance(c, (float, str)) else c, "amount": 0, "status": "?"})
    return out


def _find_contract_list_in_data(data):
    """Recursively find a list of dicts with 'id' (contract list) in dashboard-style response."""
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict) and "id" in data[0]:
        return data
    if isinstance(data, dict):
        for v in data.values():
            found = _find_contract_list_in_data(v)
            if found:
                return found
    return []


def _normalize_pin(pin: str) -> str:
    """Keep only digits; server will reject if not exactly 6."""
    return "".join(c for c in (pin or "").strip() if c.isdigit())


def register():
    permission_code = input("Permission code: ").strip()
    if not permission_code:
        print("❌ Permission code is required.")
        return
    email = input("Email: ")
    pin = getpass.getpass("PIN (6 digits): ")
    pin = _normalize_pin(pin)

    res = _loading(lambda: requests.post(f"{BASE_URL}/register", json={
        "permission_code": permission_code,
        "email": email,
        "pin": pin
    }), "Registering...")

    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        return
    if res.status_code in (200, 201):
        print("✅", data.get("message", "Registered successfully"))
    else:
        msg = data.get("detail", data) if isinstance(data, dict) else data
        if isinstance(data, dict) and data.get("error"):
            msg = f"{msg} ({data['error']})"
        print("❌", msg)


def change_pin():
    if not _require_auth():
        return
    current = getpass.getpass("Current PIN (6 digits): ")
    current = _normalize_pin(current)
    if len(current) != 6:
        print("❌ PIN must be exactly 6 digits.")
        return
    new_pin = getpass.getpass("New PIN (6 digits): ")
    new_pin = _normalize_pin(new_pin)
    if len(new_pin) != 6:
        print("❌ New PIN must be exactly 6 digits.")
        return
    confirm = getpass.getpass("Confirm new PIN: ")
    confirm = _normalize_pin(confirm)
    if new_pin != confirm:
        print("❌ New PIN and confirmation do not match.")
        return
    res = _loading(lambda: requests.post(
        f"{BASE_URL}/change-pin",
        headers=auth_headers(),
        json={"current_pin": current, "new_pin": new_pin},
        timeout=30,
    ), "Updating PIN...")
    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        return
    if res.status_code in (200, 201):
        print("✅", data.get("message", "PIN changed successfully"))
    else:
        print("❌", data.get("detail", data) if isinstance(data, dict) else res.text)


def reset_pin():
    """Forgot PIN: set new PIN using a one-time code from admin."""
    print("\n--- Reset PIN (forgot PIN) ---")
    print("Get a one-time code from the administrator, then enter it below.")
    email = input("Email: ").strip()
    if not email:
        print("❌ Email required")
        return
    code = input("Reset code (from admin): ").strip()
    if not code:
        print("❌ Reset code required")
        return
    new_pin = getpass.getpass("New PIN (6 digits): ")
    new_pin = _normalize_pin(new_pin)
    if len(new_pin) != 6:
        print("❌ PIN must be exactly 6 digits.")
        return
    confirm = getpass.getpass("Confirm new PIN: ")
    confirm = _normalize_pin(confirm)
    if new_pin != confirm:
        print("❌ PIN and confirmation do not match.")
        return
    res = _loading(lambda: requests.post(
        f"{BASE_URL}/reset-pin",
        json={"email": email, "reset_code": code, "new_pin": new_pin},
        timeout=30,
    ), "Resetting PIN...")
    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        return
    if res.status_code in (200, 201):
        print("✅", data.get("message", "PIN reset successfully. You can log in with your new PIN."))
    else:
        print("❌", data.get("detail", data) if isinstance(data, dict) else res.text)


def login():
    email = input("Email: ")
    pin = getpass.getpass("PIN (6 digits): ")
    pin = _normalize_pin(pin)

    res = _loading(lambda: requests.post(f"{BASE_URL}/login", json={
        "email": email,
        "pin": pin
    }), "Signing in...")

    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        return
    if "token" in (data or {}):
        save_token(data["token"])
        print("✅ Login successful")
    else:
        d = data or {}
        msg = d.get("detail", d) if isinstance(d, dict) else data
        if isinstance(d, dict) and d.get("error"):
            msg = f"{msg} ({d['error']})"
        print("❌", msg)


def is_logged_in():
    return load_token() is not None


def auth_headers():
    """Return auth headers if logged in, else None (does not exit)."""
    token = load_token()
    if not token:
        return None
    return {"Authorization": f"Bearer {token.strip()}"}


def _require_auth():
    """Print message and return False if not logged in; return True if logged in."""
    if not is_logged_in():
        print("❌ Please login first")
        return False
    return True


def buy():
    if not _require_auth():
        return
    res = _loading(lambda: requests.get(f"{BASE_URL}/contracts/options"), "Loading plans...")
    raw, err = _parse_response(res)
    if err:
        print(f"❌ {err or 'Could not load contract plans'}")
        return
    # API returns dict with plans, payment_address_erc20, duration_options_days, cryptomus_available; or legacy list
    if isinstance(raw, dict):
        options = raw.get("plans") or raw.get("contract_list") or []
        payment_address = raw.get("payment_address_erc20") or "0xD1D0B76F029Af8Bb5aEA1d0D77D061eDdeDfc6ff"
        duration_options = raw.get("duration_options_days") or [30, 60, 90]
        cryptomus_available = raw.get("cryptomus_available") is True
    else:
        options = raw if isinstance(raw, list) else []
        payment_address = "0xD1D0B76F029Af8Bb5aEA1d0D77D061eDdeDfc6ff"
        duration_options = [30, 60, 90]
        cryptomus_available = False
    if not options:
        print("❌ No contract plans available")
        return
    print("\n--- Contract plans (2% per day) ---")
    valid = []
    for i, p in enumerate(options):
        pid = p.get("id") if p.get("id") is not None else p.get("choice")
        if pid is None:
            pid = i + 1
        amt = p.get("amount", 0)
        try:
            amt = float(amt)
        except (TypeError, ValueError):
            amt = 0
        label = p.get("label") or (f"${int(amt)}" if amt else "?")
        valid.append(str(pid))
        print(f"{pid}. {label}")
    choice = input(f"Choose plan ({', '.join(valid)}): ").strip()
    if choice not in valid:
        print("❌ Invalid choice")
        return
    contract_choice = int(choice)
    print(f"\n--- Contract duration (refund after this period) ---")
    dur_str = ", ".join(str(d) for d in duration_options)
    duration_input = input(f"Duration in days ({dur_str}) [30]: ").strip() or "30"
    try:
        duration_days = int(duration_input)
        if duration_days not in duration_options:
            duration_days = 30
    except ValueError:
        duration_days = 30
    payload = {"contract_choice": contract_choice, "duration_days": duration_days}

    # Payment method: Cryptomus (link) or ERC20 (pay to address + tx id)
    if cryptomus_available:
        print("\n--- Payment method ---")
        print("  1. Cryptomus – pay via link (contract activates automatically)")
        print("  2. ERC20 – pay to address below, then enter wallet and transaction ID")
        pm = input("Choose (1 or 2) [1]: ").strip() or "1"
        if pm == "2":
            payload["payment_method"] = "erc20"
            print(f"\n--- Pay to this address (ERC20) ---")
            print(f"   {payment_address}")
            payment_wallet = input("Wallet address used to pay: ").strip()
            if not payment_wallet:
                print("❌ Payment wallet is required")
                return
            transaction_id = input("Transaction ID of the payment: ").strip()
            if not transaction_id:
                print("❌ Transaction ID is required")
                return
            payload["payment_wallet"] = payment_wallet
            payload["payment_tx_id"] = transaction_id
        else:
            payload["payment_method"] = "cryptomus"
    else:
        payload["payment_method"] = "erc20"
        print(f"\n--- Pay to this address (ERC20) ---")
        print(f"   {payment_address}")
        payment_wallet = input("Wallet address used to pay: ").strip()
        if not payment_wallet:
            print("❌ Payment wallet is required")
            return
        transaction_id = input("Transaction ID of the payment: ").strip()
        if not transaction_id:
            print("❌ Transaction ID is required")
            return
        payload["payment_wallet"] = payment_wallet
        payload["payment_tx_id"] = transaction_id

    res = _loading(lambda: requests.post(f"{BASE_URL}/buy", headers=auth_headers(), json=payload, timeout=30), "Processing...")
    data, err = _parse_response(res)
    if res.status_code == 401:
        print("❌ Session expired or invalid. Please log out (option 7) and log in again.")
        return
    if err:
        print(f"❌ {err}")
        return
    if isinstance(data, dict) and "contract_id" in data:
        if data.get("payment_url"):
            print(f"✅ {data.get('message', data.get('status', 'Contract created.'))}")
            print(f"   Contract ID: {data['contract_id']}, Amount: ${data.get('amount', '')}")
            print(f"   Pay here: {data['payment_url']}")
            print("   Your contract will activate automatically after payment.")
        else:
            print(f"✅ {data.get('message', data.get('status', 'Contract submitted.'))}")
            print(f"   Contract ID: {data['contract_id']}, Amount: ${data.get('amount', '')}")
            if data.get("payment_wallet"):
                print(f"   Payment wallet: {data['payment_wallet']}")
            if data.get("payment_tx_id"):
                print(f"   Transaction ID: {data['payment_tx_id']}")
            print("   Contract will be active after the system verifies your payment.")
    else:
        print(data if isinstance(data, dict) else res.text)


def _get_dashboard_data():
    """Fetch dashboard from API (same as dashboard menu). Returns (data dict or None, error or None)."""
    res = requests.get(f"{BASE_URL}/dashboard", headers=auth_headers(), timeout=SERVER_CHECK_TIMEOUT)
    if res.status_code == 401:
        return None, "Session expired. Please log out and log in again."
    data, err = _parse_response(res)
    return data, err


def dashboard():
    if not _require_auth():
        return
    data, err = _loading(lambda: _get_dashboard_data(), "Loading...")
    if err:
        print(f"❌ {err}")
        return
    print(json.dumps(data, indent=2) if data is not None else "No data")


def withdrawal_history():
    if not _require_auth():
        return
    res = _loading(lambda: requests.get(f"{BASE_URL}/withdrawals/history", headers=auth_headers()), "Loading history...")
    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        return
    if not data:
        print("No withdrawals yet.")
        return
    print("\n--- Withdrawal history ---")
    for w in data:
        created = (w.get("created_at") or "")[:19] if w.get("created_at") else "-"
        print(f"  {w.get('id')}: {w.get('amount')} -> {w.get('wallet')}  [{w.get('status')}]  {created}")
    print()


def wallets_menu():
    if not _require_auth():
        return
    while True:
        res = _loading(lambda: requests.get(f"{BASE_URL}/wallets", headers=auth_headers()), "Loading wallets...")
        data, err = _parse_response(res)
        if err:
            print(f"❌ {err}")
            return
        wallets = data or []
        print("\n--- My wallets ---")
        if not wallets:
            print("  No trusted wallets. Add one below.")
        else:
            for w in wallets:
                default = " (default)" if w.get("is_default") else ""
                label = f" - {w['label']}" if w.get("label") else ""
                print(f"  {w['id']}: {w['wallet']}{label}{default}")
        print("\n1. Add wallet  2. Set default  3. Remove wallet  4. Back")
        choice = input("Choose: ").strip()
        if choice == "1":
            wallet = input("Wallet address: ").strip()
            label = input("Label (optional): ").strip()
            is_first = len(wallets) == 0
            res = _loading(lambda: requests.post(f"{BASE_URL}/wallets", headers=auth_headers(), json={
                "wallet": wallet,
                "label": label or None,
                "is_default": is_first
            }), "Adding wallet...")
            d, e = _parse_response(res)
            if e:
                print(f"❌ {e}")
            else:
                print("✅ Wallet added")
        elif choice == "2":
            wid = input("Wallet ID to set as default: ").strip()
            if not wid.isdigit():
                print("Invalid ID")
                continue
            res = _loading(lambda: requests.put(f"{BASE_URL}/wallets/default", headers=auth_headers(), json={"wallet_id": int(wid)}), "Updating default...")
            d, e = _parse_response(res)
            if e:
                print(f"❌ {e}")
            else:
                print("✅ Default wallet updated")
        elif choice == "3":
            wid = input("Wallet ID to remove: ").strip()
            if not wid.isdigit():
                print("Invalid ID")
                continue
            res = _loading(lambda: requests.delete(f"{BASE_URL}/wallets/{wid}", headers=auth_headers()), "Removing wallet...")
            d, e = _parse_response(res)
            if e:
                print(f"❌ {e}")
            else:
                print("✅ Wallet removed")
        elif choice == "4":
            return
        else:
            print("Invalid choice")


def telegram_connect():
    """Request a Telegram link token and show the link to the user."""
    if not _require_auth():
        return
    res = requests.get(f"{BASE_URL}/telegram/status", headers=auth_headers())
    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        return
    if data and data.get("linked"):
        print("✅ Telegram already linked.")
        if data.get("telegram_username"):
            print(f"   Username: @{data['telegram_username']}")
        return
    res = requests.post(f"{BASE_URL}/telegram/link-request", headers=auth_headers())
    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        return
    if not data:
        return
    link = data.get("deep_link")
    token = data.get("link_token")
    if link:
        print("Open this link in Telegram to link your account:")
        print(f"  {link}")
    else:
        print("In Telegram, send this to the bot: /start " + (token or ""))
    print("Link expires in 15 minutes.")


def trading_accounts_menu():
    if not _require_auth():
        return
    res = _loading(lambda: requests.get(f"{BASE_URL}/trading-accounts", headers=auth_headers(), timeout=SERVER_CHECK_TIMEOUT), "Loading trading accounts...")
    data, err = _parse_response(res)
    if err:
        if err == "telegram_trading_requirement" and data:
            _print_telegram_trading_requirement(data)
            print("To get access: start a contract (Run) or choose « Pay for Account Management » in the menu.")
        else:
            print(f"❌ {err}")
            if res.status_code == 503:
                print("   (Run neon_telegram_trading_migration.sql on Neon and set METAAPI_TOKEN on the server.)")
            elif res.status_code == 502:
                print("   (MetaAPI error: check login, password, server. Server name is case-sensitive.)")
        return
    while True:
        res = _loading(lambda: requests.get(f"{BASE_URL}/trading-accounts", headers=auth_headers(), timeout=SERVER_CHECK_TIMEOUT), "Loading trading accounts...")
        data, err = _parse_response(res)
        if err:
            if err == "telegram_trading_requirement" and data:
                _print_telegram_trading_requirement(data)
            else:
                print(f"❌ {err}")
            return
        accounts = (data.get("trading_accounts") if isinstance(data, dict) else []) or []
        print("\n--- Trading accounts ---")
        if not accounts:
            print("  No accounts. Add one below.")
        else:
            for a in accounts:
                bal = a.get("balance")
                curr = a.get("currency") or ""
                bal_str = f"  {bal} {curr}".strip() if bal is not None else (a.get("error") or "—")
                print(f"  {a['id']}: {a.get('login')} @ {a.get('server')}  ({a.get('label') or '-'})  Balance: {bal_str}")
        print("\n1. Add account  2. Remove account  3. Back")
        choice = input("Choose: ").strip()
        if choice == "1":
            login = input("Login (account number): ").strip()
            password = getpass.getpass("Password: ")
            server = input("Server (e.g. Broker-Demo): ").strip()
            label = input("Label (optional): ").strip()
            platform = input("Platform (mt4 or mt5) [mt5]: ").strip().lower() or "mt5"
            if not login or not server:
                print("Login and server required.")
                continue
            res = _loading(lambda: requests.post(f"{BASE_URL}/trading-accounts", headers=auth_headers(), json={
                "login": login,
                "password": password,
                "server": server,
                "label": label or None,
                "platform": platform,
            }), "Adding account...")
            d, e = _parse_response(res)
            if e:
                if e == "telegram_trading_requirement" and d:
                    _print_telegram_trading_requirement(d)
                else:
                    print(f"❌ {e}")
                    if res.status_code == 502:
                        print("   (Check login, password, server. Use exact server name from your broker, e.g. BrokerName-Demo.)")
            else:
                print("✅ Account added. Balance will show after MetaAPI connects.")
        elif choice == "2":
            aid = input("Account ID to remove: ").strip()
            if not aid.isdigit():
                print("Invalid ID")
                continue
            res = _loading(lambda: requests.delete(f"{BASE_URL}/trading-accounts/{aid}", headers=auth_headers()), "Removing account...")
            d, e = _parse_response(res)
            if e:
                print(f"❌ {e}")
            else:
                print("✅ Account removed")
        elif choice == "3":
            return
        else:
            print("Invalid choice")


def account_management_pay():
    """Show Account Management fee and payment address; submit user's ERC20 payment for verification."""
    if not _require_auth():
        return
    res = requests.get(f"{BASE_URL}/dashboard", headers=auth_headers(), timeout=SERVER_CHECK_TIMEOUT)
    dash_data, _ = _parse_response(res)
    if isinstance(dash_data, dict) and dash_data.get("account_management_paid_at"):
        print("✅ You already have Account Management access.")
        return
    res = requests.get(f"{BASE_URL}/account-management/requirements", headers=auth_headers(), timeout=SERVER_CHECK_TIMEOUT)
    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        if res.status_code == 404:
            print("   (Server may need an update. Deploy the latest code.)")
        elif res.status_code == 503:
            print("   (Run neon_telegram_trading_migration.sql on Neon, then redeploy.)")
        return
    print("\n--- Account Management ($50 one-time) ---")
    print(data.get("summary") or data.get("message", ""))
    print(f"\n  Pay ${data.get('fee_amount', 50)} {data.get('fee_currency', 'USDT')} (ERC20) to:")
    print(f"  {data.get('payment_address_erc20', '')}")
    print("\n  After payment, enter the wallet you used and the transaction ID below.")
    print("  Access will be granted after verification.\n")
    wallet = input("Wallet address you used to pay: ").strip()
    tx_id = input("Transaction ID: ").strip()
    if not wallet or not tx_id:
        print("❌ Wallet and transaction ID required.")
        return
    res = requests.post(f"{BASE_URL}/account-management/submit-payment", headers=auth_headers(), json={
        "payment_wallet": wallet,
        "payment_tx_id": tx_id,
    }, timeout=SERVER_CHECK_TIMEOUT)
    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        if res.status_code == 503:
            print("   Run neon_telegram_trading_migration.sql on your Neon database, then redeploy the server.")
        return
    print(data.get("message") or "Submitted. You will get access after verification.")


def _fetch_bybit_balance():
    """Fetch Bybit Funding balance and withdrawable amount. Returns (balance_list, coin, withdrawableAmount, limitAmountUsd) or (None, None, None, None)."""
    res = requests.get(f"{BASE_URL}/bybit/balance", headers=auth_headers(), timeout=15)
    if res.status_code != 200:
        return None, None, None, None
    data = res.json() if res.text else {}
    return (
        data.get("balance"),
        data.get("coin"),
        data.get("withdrawableAmount"),
        data.get("limitAmountUsd"),
    )


def bybit_balance_menu():
    """Display Bybit Funding balance and actual withdrawable amount (avoids 131001)."""
    if not _require_auth():
        return
    balance_list, coin, withdrawable, limit_usd = _fetch_bybit_balance()
    if balance_list is None and withdrawable is None:
        print("Bybit balance unavailable (not configured or server error).")
        return
    c = coin or "USDT"
    print(f"Bybit Funding balance ({c}):")
    if balance_list:
        for b in balance_list:
            w = b.get("walletBalance", "0")
            t = b.get("transferBalance", "0")
            print(f"  {b.get('coin', c)}: wallet={w}, transferable={t}")
    if withdrawable is not None:
        print(f"  → Withdrawable (use this for withdrawals): {withdrawable} {c}")
        if limit_usd and float(limit_usd) > 0:
            print(f"  → Locked by deposit risk: {limit_usd} USD (wait for it to clear to withdraw more)")
    if not balance_list and withdrawable is None:
        print("  (no balance or zero)")


def withdraw():
    if not _require_auth():
        return
    dash = _loading(lambda: requests.get(f"{BASE_URL}/dashboard", headers=auth_headers()), "Loading...")
    dash_data, _ = _parse_response(dash)
    available = dash_data.get("available", 0) if isinstance(dash_data, dict) else 0
    print(f"Available for withdrawal (set by system): ${available}")
    balance_list, coin, withdrawable, limit_usd = _fetch_bybit_balance()
    if balance_list or withdrawable is not None:
        c = coin or "USDT"
        if withdrawable is not None:
            print(f"Bybit Funding {c} withdrawable: {withdrawable} (max to withdraw)")
            if limit_usd and float(limit_usd) > 0:
                print(f"  (Locked by deposit risk: {limit_usd} USD)")
        elif balance_list:
            for b in balance_list:
                print(f"Bybit Funding {b.get('coin', c)}: {b.get('walletBalance', '0')}")
    win = (dash_data.get("withdraw_window") if isinstance(dash_data, dict) else None) or {}
    print(f"Withdraw window: {win.get('message', '23:00–01:00 UTC')}")
    res = _loading(lambda: requests.get(f"{BASE_URL}/wallets", headers=auth_headers()), "Loading wallets...")
    data, err = _parse_response(res)
    wallets = (data if not err and data else []) or []
    default_wallet = next((w["wallet"] for w in wallets if w.get("is_default")), None)
    if default_wallet:
        print(f"Default wallet: {default_wallet} (leave blank to use it)")
    try:
        amount = float(input("Amount: "))
    except ValueError:
        print("❌ Enter a number")
        return
    wallet = input("Withdraw to wallet (or press Enter for default): ").strip()
    if not wallet and default_wallet:
        wallet = default_wallet
    if not wallet:
        print("❌ No wallet. Add a default in My wallets or enter one here.")
        return

    res = _loading(lambda: requests.post(
        f"{BASE_URL}/withdraw",
        headers=auth_headers(),
        json={"amount": amount, "wallet": wallet},
        timeout=30,
    ), "Processing withdrawal...")
    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        return
    if isinstance(data, dict):
        print(f"✅ {data.get('status', 'Done')}")
        if data.get("message"):
            print(f"   {data['message']}")
    else:
        print(data if isinstance(data, dict) else res.text)


def _random_hex(length=12):
    import random
    return "".join(random.choices("0123456789abcdef", k=length))


def run_contract():
    """Show which contract to run, then show a 'processing' stream with random amounts under $0.20."""
    if not _require_auth():
        return
    # Use the same dashboard fetch as the Dashboard menu (option 2)
    data, err = _loading(lambda: _get_dashboard_data(), "Loading...")
    if err:
        print(f"❌ {err}")
        return
    if not isinstance(data, dict):
        print("No contracts to run. Buy a contract first.")
        return
    # Same logic as dashboard: "contracts" count tells us if user has contracts
    contracts_count = data.get("contracts") or 0
    if contracts_count <= 0:
        print("No contracts to run. Buy a contract first.")
        return
    # Get contract list: same source as dashboard (contract_list from dashboard response)
    raw_list = (
        data.get("contract_list")
        or data.get("contractList")
        or (data.get("contracts") if isinstance(data.get("contracts"), list) else None)
        or ((data.get("data") or {}).get("contract_list") if isinstance(data.get("data"), dict) else None)
    )
    if not raw_list:
        raw_list = _find_contract_list_in_data(data)
    contract_list = _normalize_contract_list(raw_list or [])
    # If dashboard didn't include list but says we have contracts, fetch from GET /contracts
    if not contract_list and contracts_count > 0:
        headers = auth_headers() or {}
        headers["Accept"] = "application/json"
        for path in ("/contracts", "/contracts/"):
            if contract_list:
                break
            try:
                res2 = requests.get(f"{BASE_URL.rstrip('/')}{path}", headers=headers, timeout=SERVER_CHECK_TIMEOUT)
                if res2.status_code == 200:
                    data2, err2 = _parse_response(res2)
                    if not err2:
                        if isinstance(data2, list):
                            contract_list = _normalize_contract_list(data2)
                        elif isinstance(data2, dict):
                            contract_list = _normalize_contract_list(
                                data2.get("contract_list") or data2.get("data") or data2.get("contracts") or []
                            )
                            if not contract_list:
                                contract_list = _normalize_contract_list(_find_contract_list_in_data(data2))
                            if contract_list:
                                data["active_run_contract_id"] = data2.get("active_run_contract_id")
            except Exception:
                pass
    if not contract_list:
        print(f"Dashboard shows {contracts_count} contract(s) but the list could not be loaded. Try again or update the app.")
        return
    # Backend says which contract (if any) has an active run — show that so display matches "already running" check
    active_run_contract_id = data.get("active_run_contract_id")
    print("\n--- Run contract ---")
    for c in contract_list:
        cid = c.get("id")
        status = "running" if cid == active_run_contract_id else (c.get("status") or "?")
        print(f"  {cid}. Contract #{cid} — ${c.get('amount', 0):.0f} ({status})")
    choice = input("Choose contract ID to run (or Enter to cancel): ").strip()
    if not choice:
        return
    cid = None
    for c in contract_list:
        if str(c.get("id")) == choice:
            cid = c.get("id")
            break
    if cid is None:
        print("❌ Invalid contract ID")
        return

    # Start run on server (earnings saved there; survives disconnect/power off)
    import random
    res = _loading(lambda: requests.post(
        f"{BASE_URL}/run/start",
        headers=auth_headers(),
        json={"contract_id": cid},
        timeout=30
    ), "Starting run...")
    data, err = _parse_response(res)
    if err or res.status_code != 200:
        print(f"❌ {err or data.get('detail', res.text)}")
        return
    run_id = data.get("run_id")
    if run_id is None:
        print("❌ Could not start run.")
        return
    run_max_seconds = 22 * 3600
    print("\n--- Run started (22 hours) ---")
    print("Earnings are saved on the server. If you disconnect or power off, earnings are kept.")
    print("Press Enter at any time to stop and add earnings to your withdrawable balance.\n")

    stop_requested = False
    def wait_for_stop():
        input()
        nonlocal stop_requested
        stop_requested = True
    t = threading.Thread(target=wait_for_stop, daemon=True)
    t.start()

    start_time = time.time()
    last_heartbeat = start_time
    heartbeat_interval = 120  # 2 minutes
    delays = [1, 2, 5, 10]
    # Random display amounts under $0.20 (actual earnings are saved on server every 10 min)
    display_amounts = [0.02, 0.05, 0.07, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.03, 0.06, 0.09, 0.11, 0.14, 0.17]
    while (time.time() - start_time) < run_max_seconds and not stop_requested:
        time.sleep(random.choice(delays))
        tx_id = _random_hex(8) + "..." + _random_hex(8)
        amt = random.choice(display_amounts)
        print(f"  [{time.strftime('%H:%M:%S')}] Processing transaction {tx_id}  +${amt:.2f}")
        # Heartbeat every 2 min so server tracks progress (earnings safe if connection lost)
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            try:
                r = requests.post(
                    f"{BASE_URL}/run/heartbeat",
                    headers=auth_headers(),
                    json={"run_id": run_id},
                    timeout=15
                )
                d, _ = _parse_response(r)
                if d and d.get("active") and d.get("earnings_so_far") is not None:
                    print(f"  ... Earnings so far: ${d.get('earnings_so_far', 0)}")
                if d and d.get("ended"):
                    print(f"\n✅ Run completed (22 hours). ${d.get('earnings_added', 0)} added to withdrawable balance.")
                    return
            except Exception:
                pass
            last_heartbeat = now

    # Stop run and credit earnings
    try:
        r = requests.post(
            f"{BASE_URL}/run/stop",
            headers=auth_headers(),
            json={"run_id": run_id},
            timeout=15
        )
        d, _ = _parse_response(r)
        if d and d.get("earnings_added") is not None:
            print(f"\n✅ Run stopped. ${d.get('earnings_added', 0)} added to your withdrawable balance.")
        else:
            print("\n✅ Run stopped." + (f" {d.get('message', '')}" if isinstance(d, dict) else ""))
    except Exception as e:
        print(f"\n✅ Run stopped. (Server stop request failed: {e}. Earnings may have been saved by heartbeat.)")


def stop():
    if not _require_auth():
        return
    contract_id = int(input("Contract ID: "))
    pin = getpass.getpass("Confirm PIN (6 digits): ")
    pin = _normalize_pin(pin)

    res = _loading(lambda: requests.post(
        f"{BASE_URL}/stop",
        headers=auth_headers(),
        json={
            "contract_id": contract_id,
            "pin": pin
        }
    ), "Stopping contract...")
    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        return
    print(data if isinstance(data, dict) else res.text)


def refund_menu():
    """Request a refund for a contract or view refund request status."""
    if not _require_auth():
        return
    while True:
        print("\n--- Refund ---")
        print("1. Request refund")
        print("2. View refund status")
        print("3. Back")
        sub = input("Choose: ").strip()
        if sub == "3":
            return
        if sub == "1":
            res = _loading(lambda: requests.get(f"{BASE_URL}/contracts", headers=auth_headers(), timeout=SERVER_CHECK_TIMEOUT), "Loading contracts...")
            data, err = _parse_response(res)
            if err:
                print(f"❌ {err}")
                continue
            clist = (data.get("contract_list") if isinstance(data, dict) else []) or []
            if not clist:
                print("No contracts found. Buy a contract first.")
                continue
            print("\nYour contracts:")
            for c in clist:
                print(f"  ID {c.get('id')}: ${c.get('amount', 0)} — status: {c.get('status', '?')}")
            cid_str = input("Contract ID to refund: ").strip()
            try:
                cid = int(cid_str)
            except ValueError:
                print("❌ Enter a number")
                continue
            reason = input("Reason for refund (optional): ").strip()
            wallet = input("Wallet address to receive refund: ").strip()
            if not wallet:
                print("❌ Wallet is required")
                continue
            res = _loading(lambda: requests.post(
                f"{BASE_URL}/refund-request",
                headers=auth_headers(),
                json={"contract_id": cid, "reason": reason or None, "wallet": wallet},
                timeout=30,
            ), "Submitting...")
            data, err = _parse_response(res)
            if err:
                print(f"❌ {err}")
                continue
            print("✅ Refund request submitted.")
            if isinstance(data, dict):
                print(f"   Status: {data.get('status', 'pending')}")
                if data.get("message"):
                    print(f"   {data['message']}")
            continue
        if sub == "2":
            res = _loading(lambda: requests.get(f"{BASE_URL}/refund-requests", headers=auth_headers(), timeout=SERVER_CHECK_TIMEOUT), "Loading status...")
            data, err = _parse_response(res)
            if err:
                print(f"❌ {err}")
                continue
            requests_list = (data.get("refund_requests") if isinstance(data, dict) else []) or []
            if not requests_list:
                print("No refund requests.")
                continue
            print("\n--- Refund requests ---")
            for r in requests_list:
                w = r.get("wallet") or ""
                w_display = w[:24] + "..." if len(w) > 24 else w
                print(f"  ID {r.get('id')}  Contract {r.get('contract_id')}  Wallet: {w_display}  Status: {r.get('status', '?')}")
                print(f"    Created: {r.get('created_at', '')}  Updated: {r.get('updated_at', '')}")
                if r.get("admin_notes"):
                    print(f"    Admin: {r['admin_notes']}")
            continue
        print("Invalid choice")


def _fetch_trading_available():
    """Fetch whether Trading accounts (MetaAPI) is configured. Caches result until logout."""
    global _trading_available
    if _trading_available is not None:
        return _trading_available
    try:
        res = _loading(lambda: requests.get(f"{BASE_URL}/trading-accounts/available", headers=auth_headers(), timeout=10), "Checking...")
        if res.status_code == 200:
            data = res.json() if res.text else {}
            _trading_available = bool(data.get("available"))
        else:
            _trading_available = False
    except Exception:
        _trading_available = False
    return _trading_available


def menu():
    global _trading_available
    while True:
        clear_screen()
        print_header()
        logged_in = is_logged_in()
        if logged_in:
            _fetch_trading_available()
            print("1. Buy Contract")
            print("2. Dashboard")
            print("3. Withdraw")
            print("4. Withdrawal history")
            print("5. My wallets")
            print("6. Stop Contract")
            print("7. Run")
            print("8. Refund")
            if _trading_available:
                print("9. Trading accounts")
                print("10. Change PIN")
                print("11. Log out")
                print("12. Exit")
            else:
                print("9. Change PIN")
                print("10. Log out")
                print("11. Exit")
        else:
            print("1. Register")
            print("2. Login")
            print("3. Forgot PIN")
            print("4. Buy Contract")
            print("5. Dashboard")
            print("6. Withdraw")
            print("7. Stop Contract")
            print("8. Exit")

        choice = input("Choose: ").strip()

        if logged_in:
            if choice == "1":
                buy()
            elif choice == "2":
                dashboard()
            elif choice == "3":
                withdraw()
            elif choice == "4":
                withdrawal_history()
            elif choice == "5":
                wallets_menu()
            elif choice == "6":
                stop()
            elif choice == "7":
                run_contract()
            elif choice == "8":
                refund_menu()
            elif _trading_available and choice == "9":
                trading_accounts_menu()
            elif (_trading_available and choice == "10") or (not _trading_available and choice == "9"):
                change_pin()
            elif (_trading_available and choice == "11") or (not _trading_available and choice == "10"):
                logout()
            elif _trading_available and choice == "12":
                break
            elif not _trading_available and choice == "11":
                break
            else:
                print("Invalid choice")
        else:
            if choice == "1":
                register()
            elif choice == "2":
                login()
            elif choice == "3":
                reset_pin()
            elif choice == "4":
                buy()
            elif choice == "5":
                dashboard()
            elif choice == "6":
                withdraw()
            elif choice == "7":
                stop()
            elif choice == "8":
                break
            else:
                print("Invalid choice")


def _pause_if_exe():
    """Keep console open when exe exits so user can read errors (PyInstaller sets sys.frozen)."""
    if getattr(sys, "frozen", False):
        input("\nPress Enter to close...")


def main():
    """Entry point for the contract CLI (e.g. from pip-installed script)."""
    try:
        _loading(lambda: _check_server(), "Checking server...")
        menu()
    except SystemExit:
        _pause_if_exe()
        raise
    except Exception as e:
        print("Error:", e)
        import traceback
        traceback.print_exc()
        _pause_if_exe()
        sys.exit(1)
    _pause_if_exe()


if __name__ == "__main__":
    main()