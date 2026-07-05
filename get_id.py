"""Standalone helper: prints the chat IDs your customer bot can currently see.

Usage:
  1. Add your CUSTOMER bot as an admin of your channel/group.
  2. Post any message in that channel/group (or forward one of its posts to the bot).
  3. Run this (get_id.bat). It prints every chat it has seen, with the -100... ID.

Uses only the Python standard library so it runs without the full install.
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path


def read_token() -> str:
    # Environment variable wins (works in Colab / Railway / anywhere).
    env_token = os.environ.get("CUSTOMER_BOT_TOKEN", "").strip()
    if env_token:
        return env_token
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        sys.exit(
            "No CUSTOMER_BOT_TOKEN found. Either set it as an environment variable, "
            "or run setup.bat and fill it into .env."
        )
    for line in env_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*CUSTOMER_BOT_TOKEN\s*=\s*(.+)\s*$", line)
        if match:
            token = match.group(1).strip()
            if token:
                return token
    sys.exit("CUSTOMER_BOT_TOKEN is empty in .env. Fill it in, then run this again.")


def main() -> None:
    token = read_token()
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            data = json.load(response)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Could not reach Telegram: {exc}")

    if not data.get("ok"):
        sys.exit(f"Telegram error: {data}")

    seen: dict[int, str] = {}
    for update in data.get("result", []):
        for key in ("message", "channel_post", "my_chat_member"):
            obj = update.get(key)
            if obj and "chat" in obj:
                chat = obj["chat"]
                title = chat.get("title") or chat.get("username") or chat.get("first_name") or "(no name)"
                seen[chat["id"]] = f"{title}  [{chat.get('type')}]"

    print()
    if not seen:
        print("No chats seen yet.")
        print("Make sure you:")
        print("  1. Added the customer bot as ADMIN of your channel/group, and")
        print("  2. Posted a message there (or forwarded one of its posts to the bot),")
        print("then run this again.")
        return

    print("Chats your bot can see (use the number as DESTINATION_CHAT_ID):")
    print("-" * 60)
    for chat_id, label in seen.items():
        print(f"  {chat_id}   {label}")
    print("-" * 60)
    print("Copy the number for YOUR channel into .env as DESTINATION_CHAT_ID")


if __name__ == "__main__":
    main()
