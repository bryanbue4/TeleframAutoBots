# TeleframAutoBots

Personal Telegram automation system: curates content from public channels/groups
you've joined into your own community, moderates and greets members with AI,
and gives you a private assistant bot for reports and alerts.

## Architecture

| Concern | Choice | Why |
|---|---|---|
| Reading joined channels | Telethon (user session) | Bot API can only read chats a bot administers - not arbitrary channels you've personally joined |
| Bot structure | Two bots | `customer_bot` handles your group/channel members; `admin_bot` is your private assistant, kept separate for isolation and clarity |
| Language | Python | Telethon is the standard for the user-session piece; one ecosystem covers Telegram + all AI providers |
| Hosting | Railway (primary), Replit (backup) | Both support always-on processes, unlike serverless platforms (Vercel). Note: neither has an indefinite free tier for an always-on worker - expect to eventually need a small paid tier or a keep-alive workaround |
| Storage | SQLite (`data/app.db`) | Zero-setup, fits the 10-100 users/day scale |
| AI | OpenRouter gateway | One API key covers Anthropic/OpenAI/DeepSeek behind a single integration, making model/cost choices a config change |

## Components

- `app/content_sync/listener.py` - Telethon client that watches `SOURCE_CHATS`
  and forwards new posts to `DESTINATION_CHAT_ID`, in order, once each, with a
  delay between forwards.
- `app/customer_bot/bot.py` - handles join requests (auto-approves, flags
  unusual ones for your manual review), greets and chats with members via AI,
  collects name/city, flags likely duplicates and suspicious behavior.
- `app/admin_bot/bot.py` - your private assistant: `/approve <id>`,
  `/reject <id>`, `/remove <id>`, `/report`, and free-form AI chat.
- `app/scheduler/jobs.py` - posts AI-generated good morning/good night messages
  (with a photo from `assets/morning` or `assets/night` if present), nudges
  members to reply, and sends you the twice-daily AI-summarized report.
- `app/ai/router.py` - thin OpenRouter wrapper; swap `OPENROUTER_MODEL` per call
  to control cost/quality.

## Quick start (Windows, easiest)

1. Gather your credentials (see the numbered list below).
2. Double-click **`setup.bat`** - it creates the environment, installs everything,
   and opens `.env` in Notepad for you to fill in. Save and close.
3. Double-click **`run.bat`** to start the bot. On first run it asks for your
   phone number and a Telegram login code (once only).

## Setup (details / other platforms)

1. Create two bots with [@BotFather](https://t.me/BotFather): one for members
   (`CUSTOMER_BOT_TOKEN`), one for yourself (`ADMIN_BOT_TOKEN`). Add the
   customer bot as **admin** of your group/channel (needed to approve join
   requests, post messages, and manage members).
2. Enable "approve new members" on your group/channel so join requests flow
   through the bot.
3. Get `TELETHON_API_ID`/`TELETHON_API_HASH` from https://my.telegram.org for
   your own Telegram account - this is the account whose joined channels get
   read from. The first run will prompt for a login code in the terminal.
4. Copy `.env.example` to `.env` and fill in all values, including
   `SOURCE_CHATS` (comma-separated usernames/IDs you've joined) and
   `DESTINATION_CHAT_ID` (your own group/channel).
5. `pip install -r requirements.txt`
6. `python -m app.main`

### Finding `DESTINATION_CHAT_ID`

The channel/group ID (a `-100...` number) is awkward to find by hand. After you
have set `CUSTOMER_BOT_TOKEN` in `.env`, added the customer bot as admin of your
channel, and posted a message there, run the helper: double-click `get_id.bat`
(or `python get_id.py`). It prints every chat the bot can see with its ID -
copy your channel's number into `DESTINATION_CHAT_ID`.

## Data collected

Per member: Telegram user ID, name, city, join/approval status, message count,
duplicate/suspicious flags. Stored locally in SQLite - nothing is sent
anywhere except to the AI provider for generating replies/summaries.

## Known limitations (by design, for now)

- Suspicious-behavior detection is rule-based (links, no username, no
  response). Adaptive/learning detection from historical behavior is a
  deliberate phase 2, once there's real usage data to learn from.
- Content copying uses Telethon `forward_messages`, which preserves the
  "forwarded from" attribution rather than disguising the source.
