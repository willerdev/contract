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
    """Return (data dict or None, error message or None). Handles empty/non-JSON body. Data can be dict or list."""
    if not res.text or not res.text.strip():
        return None, f"Server returned empty response (status {res.status_code})"
    try:
        return res.json(), None
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
    raw, err = _parse_response(res)
    if err:
        print(f"❌ {err or 'Could not load contract plans'}")
        return
    # API returns dict with plans, payment_address_erc20, duration_options_days; or legacy list
    if isinstance(raw, dict):
        options = raw.get("plans") or raw.get("contract_list") or []
        payment_address = raw.get("payment_address_erc20") or "0xD1D0B76F029Af8Bb5aEA1d0D77D061eDdeDfc6ff"
        duration_options = raw.get("duration_options_days") or [30, 60, 90]
    else:
        options = raw if isinstance(raw, list) else []
        payment_address = "0xD1D0B76F029Af8Bb5aEA1d0D77D061eDdeDfc6ff"
        duration_options = [30, 60, 90]
    if not options:
        print("❌ No contract plans available")
        return
    print("\n--- Pay to this address (ERC20 network) ---")
    print(f"   {payment_address}")
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
            "duration_days": duration_days,
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
    data, err = _get_dashboard_data()
    if err:
        print(f"❌ {err}")
        return
    print(json.dumps(data, indent=2) if data is not None else "No data")


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
    # Use the same dashboard fetch as the Dashboard menu (option 2)
    data, err = _get_dashboard_data()
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
            except Exception:
                pass
    if not contract_list:
        print(f"Dashboard shows {contracts_count} contract(s) but the list could not be loaded. Try again or update the app.")
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

    # Start run on server (earnings saved there; survives disconnect/power off)
    import random
    import time
    import threading
    res = requests.post(
        f"{BASE_URL}/run/start",
        headers=auth_headers(),
        json={"contract_id": cid},
        timeout=30
    )
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
    while (time.time() - start_time) < run_max_seconds and not stop_requested:
        time.sleep(random.choice(delays))
        tx_id = _random_hex(8) + "..." + _random_hex(8)
        print(f"  [{time.strftime('%H:%M:%S')}] Processing transaction {tx_id}  +$0.02")
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