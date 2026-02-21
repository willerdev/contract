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
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    print("✅ Logged out")


def _parse_response(res):
    """Return (data dict or None, error message or None). Handles empty/non-JSON body."""
    if not res.text or not res.text.strip():
        return None, f"Server returned empty response (status {res.status_code})"
    try:
        return res.json(), None
    except json.JSONDecodeError:
        return None, f"Server response not JSON (status {res.status_code}): {res.text[:200]}"


def _normalize_pin(pin: str) -> str:
    """Keep only digits; server will reject if not exactly 6."""
    return "".join(c for c in (pin or "").strip() if c.isdigit())


def register():
    email = input("Email: ")
    pin = getpass.getpass("PIN (6 digits): ")
    pin = _normalize_pin(pin)

    res = requests.post(f"{BASE_URL}/register", json={
        "email": email,
        "pin": pin
    })

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


def login():
    email = input("Email: ")
    pin = getpass.getpass("PIN (6 digits): ")
    pin = _normalize_pin(pin)

    res = requests.post(f"{BASE_URL}/login", json={
        "email": email,
        "pin": pin
    })

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
    res = requests.get(f"{BASE_URL}/contracts/options")
    options, err = _parse_response(res)
    if err or not isinstance(options, list) or not options:
        print(f"❌ {err or 'Could not load contract plans'}")
        return
    print("\n--- Contract plans (2% per day) ---")
    valid = []
    for i, p in enumerate(options):
        # Support both "id" (new API) and "choice" (legacy), else use 1-based index
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
    choice = input(f"Choose ({', '.join(valid)}): ").strip()
    if choice not in valid:
        print("❌ Invalid choice")
        return
    contract_choice = int(choice)
    payment_wallet = input("Wallet address used to pay: ").strip()
    if not payment_wallet:
        print("❌ Payment wallet is required")
        return
    transaction_id = input("Transaction ID of the payment: ").strip()
    if not transaction_id:
        print("❌ Transaction ID is required")
        return
    res = requests.post(
        f"{BASE_URL}/buy",
        headers=auth_headers(),
        json={
            "contract_choice": contract_choice,
            "payment_wallet": payment_wallet,
            "payment_tx_id": transaction_id,
        }
    )
    data, err = _parse_response(res)
    if res.status_code == 401:
        print("❌ Session expired or invalid. Please log out (option 7) and log in again.")
        return
    if err:
        print(f"❌ {err}")
        return
    if isinstance(data, dict) and "contract_id" in data:
        print(f"✅ {data.get('message', data.get('status', 'Contract submitted.'))}")
        print(f"   Contract ID: {data['contract_id']}, Amount: ${data.get('amount', '')}")
        if data.get("payment_wallet"):
            print(f"   Payment wallet: {data['payment_wallet']}")
        if data.get("payment_tx_id"):
            print(f"   Transaction ID: {data['payment_tx_id']}")
        if not data.get("payment_wallet") or not data.get("payment_tx_id"):
            print("   ⚠️  Warning: Payment info may not be saved.")
        else:
            print("   ✓ Payment info saved successfully")
        print("   Contract will be active after the system verifies your payment.")
    else:
        print(data if isinstance(data, dict) else res.text)


def dashboard():
    if not _require_auth():
        return
    res = requests.get(
        f"{BASE_URL}/dashboard",
        headers=auth_headers()
    )
    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        return
    print(json.dumps(data, indent=2) if data is not None else res.text)


def withdrawal_history():
    if not _require_auth():
        return
    res = requests.get(f"{BASE_URL}/withdrawals/history", headers=auth_headers())
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
        res = requests.get(f"{BASE_URL}/wallets", headers=auth_headers())
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
            res = requests.post(f"{BASE_URL}/wallets", headers=auth_headers(), json={
                "wallet": wallet,
                "label": label or None,
                "is_default": is_first
            })
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
            res = requests.put(f"{BASE_URL}/wallets/default", headers=auth_headers(), json={"wallet_id": int(wid)})
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
            res = requests.delete(f"{BASE_URL}/wallets/{wid}", headers=auth_headers())
            d, e = _parse_response(res)
            if e:
                print(f"❌ {e}")
            else:
                print("✅ Wallet removed")
        elif choice == "4":
            return
        else:
            print("Invalid choice")


def withdraw():
    if not _require_auth():
        return
    dash = requests.get(f"{BASE_URL}/dashboard", headers=auth_headers())
    dash_data, _ = _parse_response(dash)
    available = dash_data.get("available", 0) if isinstance(dash_data, dict) else 0
    print(f"Available for withdrawal (set by system): ${available}")
    res = requests.get(f"{BASE_URL}/wallets", headers=auth_headers())
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

    res = requests.post(
        f"{BASE_URL}/withdraw",
        headers=auth_headers(),
        json={
            "amount": amount,
            "wallet": wallet
        }
    )
    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        return
    print(data if isinstance(data, dict) else res.text)


def _random_hex(length=12):
    import random
    return "".join(random.choices("0123456789abcdef", k=length))


def run_contract():
    """Show which contract to run, then show a 'processing' stream of fake $0.02 transactions."""
    if not _require_auth():
        return
    timeout = 30
    headers = auth_headers()
    contract_list = []
    # Try dashboard first (has contract_list)
    res = requests.get(f"{BASE_URL}/dashboard", headers=headers, timeout=timeout)
    if res.status_code == 401:
        print("❌ Session expired or invalid. Please log out and log in again.")
        return
    data, err = _parse_response(res)
    if not err and isinstance(data, dict):
        contract_list = data.get("contract_list") or data.get("contractList") or []
    # Fallback: if contract_list empty but dashboard says we have contracts, try GET /contracts
    if not contract_list and isinstance(data, dict) and (data.get("contracts") or 0) > 0:
        res2 = requests.get(f"{BASE_URL}/contracts", headers=headers, timeout=timeout)
        if res2.status_code == 401:
            print("❌ Session expired or invalid. Please log out and log in again.")
            return
        data2, err2 = _parse_response(res2)
        if not err2 and isinstance(data2, list):
            contract_list = data2
    if not contract_list:
        if err:
            print(f"❌ {err}")
        else:
            print("No contracts to run. Buy a contract first.")
        return
    print("\n--- Run contract ---")
    for c in contract_list:
        print(f"  {c.get('id')}. Contract #{c.get('id')} — ${c.get('amount', 0):.0f} ({c.get('status', '?')})")
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
    print("\nLooking and processing transactions...\n")
    import random
    import time
    delays = [1, 2, 5, 10]
    n = random.randint(8, 15)
    for i in range(n):
        time.sleep(random.choice(delays))
        tx_id = _random_hex(8) + "..." + _random_hex(8)
        print(f"  [{time.strftime('%H:%M:%S')}] Processing transaction {tx_id}  +$0.02")
    print("\nRun completed.")


def stop():
    if not _require_auth():
        return
    contract_id = int(input("Contract ID: "))
    pin = getpass.getpass("Confirm PIN (6 digits): ")
    pin = _normalize_pin(pin)

    res = requests.post(
        f"{BASE_URL}/stop",
        headers=auth_headers(),
        json={
            "contract_id": contract_id,
            "pin": pin
        }
    )
    data, err = _parse_response(res)
    if err:
        print(f"❌ {err}")
        return
    print(data if isinstance(data, dict) else res.text)


def menu():
    while True:
        logged_in = is_logged_in()
        print("\n===== MENU =====")
        if logged_in:
            print("1. Buy Contract")
            print("2. Dashboard")
            print("3. Withdraw")
            print("4. Withdrawal history")
            print("5. My wallets")
            print("6. Stop Contract")
            print("7. Run")
            print("8. Log out")
            print("9. Exit")
        else:
            print("1. Register")
            print("2. Login")
            print("3. Buy Contract")
            print("4. Dashboard")
            print("5. Withdraw")
            print("6. Stop Contract")
            print("7. Exit")

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
                logout()
            elif choice == "9":
                break
            else:
                print("Invalid choice")
        else:
            if choice == "1":
                register()
            elif choice == "2":
                login()
            elif choice == "3":
                buy()
            elif choice == "4":
                dashboard()
            elif choice == "5":
                withdraw()
            elif choice == "6":
                stop()
            elif choice == "7":
                break
            else:
                print("Invalid choice")


if __name__ == "__main__":
    print("Checking server... (may take up to a minute if it's waking up)")
    _check_server()
    menu()