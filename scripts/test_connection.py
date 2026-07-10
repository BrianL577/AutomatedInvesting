#!/usr/bin/env python3
"""Confirms the bot is actually wired up to your Tradovate paper account
before you trust the live strategy runner: authenticates, lists every
resolved account, and places one small bracket test order (default 4pt
stop / 6pt target, 1 contract) on the account you choose.

Usage:
    python scripts/test_connection.py --list-accounts
    python scripts/test_connection.py --account "DEMO12345" --direction Buy
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.config import load_config
from jj_bot.test_trade import list_accounts, run_connection_test


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-accounts", action="store_true", help="Just resolve and print accounts, no trade")
    parser.add_argument("--account", default=None, help="Account name to test (defaults to the first resolved account)")
    parser.add_argument("--direction", default="Buy", choices=["Buy", "Sell"])
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    if cfg.tradovate.env != "demo":
        raise SystemExit("TRADOVATE_ENV must be 'demo'. Refusing to run.")

    if args.list_accounts:
        accounts = list_accounts(cfg)
        print(f"Resolved {len(accounts)} account(s):")
        for a in accounts:
            print(f"  - {a.name} (id={a.id}, active={a.active})")
        return

    print("Placing test trade...")
    result = run_connection_test(cfg, account_name=args.account, direction=args.direction)
    print(f"Accounts found:   {result.accounts}")
    print(f"Tested account:   {result.tested_account}")
    print(f"Contract:         {result.contract_symbol}")
    print(f"Order response:   {result.order_response}")
    print("\nCheck this order in your Tradovate demo account to confirm it filled.")
    print("It was also logged to dashboard/data/trades.json (source=connection_test).")


if __name__ == "__main__":
    main()
