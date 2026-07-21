"""Generate a Telethon session string (run this ONCE - no permanent install).

NOTE: This telethon.sync version is for a plain terminal/shell. It does NOT
work in Google Colab or Jupyter, which already run an event loop - use the
async cell in RAILWAY.md (Part 1) there instead.

The printed string lets the bot log into your Telegram account on a headless
host (Railway) without an interactive login. Treat it like a password: anyone
with it can act as your Telegram account. Paste it into Railway as the
TELETHON_SESSION_STRING environment variable - never commit it.

Colab steps:
  1. Open https://colab.research.google.com  ->  New notebook
  2. In a cell run:   !pip install telethon
  3. Paste this whole file into the next cell and run it
  4. Enter your api_id, api_hash, phone number, and the code Telegram sends
  5. Copy the printed SESSION STRING
"""

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("Enter your TELETHON_API_ID: ").strip())
api_hash = input("Enter your TELETHON_API_HASH: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\n" + "=" * 60)
    print("YOUR SESSION STRING (copy the whole line below):")
    print("=" * 60)
    print(client.session.save())
    print("=" * 60)
    print("Paste it into Railway as TELETHON_SESSION_STRING. Keep it secret.")
