#!/usr/bin/env python3
"""
Test Bybit withdrawal (real transfer). Uses .env BYBIT_* keys.
Address must be in Bybit address book. Run: python3 test_bybit_withdraw.py [amount] [address]
Or run without args for interactive prompts.
"""
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import bybit


def _my_ip():
    try:
        import requests
        r = requests.get("https://api.ipify.org?format=json", timeout=5)
        r.raise_for_status()
        return (r.json() or {}).get("ip") or "unknown"
    except Exception:
        return "unknown"


def main():
    if not bybit.is_configured():
        print("Missing BYBIT_API_KEY or BYBIT_API_SECRET in .env")
        return 1

    coin = bybit._withdraw_coin()
    chain = bybit._withdraw_chain()
    my_ip = _my_ip()

    print("Bybit test withdrawal (real transfer)")
    print(f"Coin: {coin}, Chain: {chain}")
    print(f"This request is sent from IP: {my_ip}  (must be whitelisted in Bybit)")
    withdrawable, w_err = bybit.get_withdrawable_amount(coin)
    if withdrawable and not w_err:
        w_amt = withdrawable.get("withdrawableAmount") or "0"
        limit_usd = withdrawable.get("limitAmountUsd") or "0"
        print(f"Withdrawable (use as max to avoid 131001): {w_amt} {coin}")
        if limit_usd and float(limit_usd) > 0:
            print(f"  Locked by deposit risk: {limit_usd} USD")
    elif w_err:
        print(f"(Withdrawable amount unavailable: {w_err})")
    print("Note: With feeType=1, amount is deducted from balance; recipient gets amount minus network fee. Min amount and 10s/chain/coin limit apply.")
    print()

    if len(sys.argv) >= 3:
        amount = sys.argv[1].strip()
        address = sys.argv[2].strip()
    elif len(sys.argv) == 2:
        amount = sys.argv[1].strip()
        address = (input("Withdraw to address: ") or "").strip()
    else:
        amount = (input("Amount: ") or "").strip()
        address = (input("Withdraw to address: ") or "").strip()

    if not amount or not address:
        print("Need amount and address.")
        return 1
    try:
        float(amount)
    except ValueError:
        print("Amount must be a number.")
        return 1

    print(f"Sending {amount} {coin} to {address} on {chain}...")
    wid, err = bybit.create_withdraw(address, amount, request_id="test-withdraw-script")
    if err:
        print(f"Withdrawal failed: {err}")
        return 1
    print(f"OK – Bybit withdrawal id: {wid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
