#!/usr/bin/env python3
"""Sends an update/rollback alert email via jj_bot.alerts. Used by
scripts/self_update.ps1 so a failed or rolled-back self-update doesn't go
unnoticed on an unattended machine. A thin wrapper (rather than inlining
this into the PowerShell script's own `python -c ...`) so subject/body text
containing quotes or newlines (git output, tracebacks) doesn't have to
survive being embedded inside a one-line Python string literal.

Usage:
    python scripts/send_update_alert.py "<subject>" "<body>"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.alerts import send_crash_alert


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: send_update_alert.py <subject> <body>", file=sys.stderr)
        raise SystemExit(1)
    send_crash_alert(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
