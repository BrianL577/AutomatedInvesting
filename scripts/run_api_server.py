#!/usr/bin/env python3
"""Runs the bot API server that the dashboard's Test Trade panel calls.

Usage:
    python scripts/run_api_server.py
    # or: uvicorn jj_bot.api_server:app --host 0.0.0.0 --port 8787
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main() -> None:
    host = os.getenv("BOT_API_HOST", "0.0.0.0")
    port = int(os.getenv("BOT_API_PORT", "8787"))
    uvicorn.run("jj_bot.api_server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
