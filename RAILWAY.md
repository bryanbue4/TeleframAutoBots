# Deploying to Railway (no laptop needed)

This runs the bots 24/7 in the cloud. You only need a browser and your phone.
There are three parts: generate a Telegram session string, create the Railway
service, and set the environment variables.

---

## Part 1 - Generate your Telegram session string (browser only)

Railway has no interactive terminal, so Telethon can't do its phone-login there.
Instead you generate a login "session string" once, in a browser, and give it to
Railway as a secret.

1. Open <https://colab.research.google.com> and click **New notebook**
   (sign in with any Google account - nothing installs on your computer).
2. In the first cell, type this and run it (click the play button):
   ```
   !pip install telethon
   ```
3. In the next cell, paste this (Colab already runs an event loop, so it needs
   the `await` style - the `telethon.sync` style in `generate_session.py` only
   works in a plain terminal, not in Colab):
   ```python
   from telethon import TelegramClient
   from telethon.sessions import StringSession
   from telethon.errors import SessionPasswordNeededError

   api_id = int(input("TELETHON_API_ID: ").strip())
   api_hash = input("TELETHON_API_HASH: ").strip()

   client = TelegramClient(StringSession(), api_id, api_hash)
   await client.connect()

   if not await client.is_user_authorized():
       phone = input("Phone (with country code, e.g. +91...): ").strip()
       await client.send_code_request(phone)
       code = input("Code Telegram sent you: ").strip()
       try:
           await client.sign_in(phone=phone, code=code)
       except SessionPasswordNeededError:
           pw = input("2FA password: ").strip()
           await client.sign_in(password=pw)

   print("\n" + "=" * 60)
   print("SESSION STRING (copy the whole line):")
   print("=" * 60)
   print(client.session.save())
   print("=" * 60)
   ```
4. Run the cell. It asks for:
   - your **api_id** and **api_hash** (from my.telegram.org)
   - your **phone number** (with country code, e.g. `+91...`)
   - the **login code** Telegram sends you
   - your **2FA password** if you have one
5. It prints a long **SESSION STRING**. Copy the whole thing - you'll paste it
   into Railway in Part 3. Treat it like a password.

---

## Part 2 - Create the Railway service (browser only)

1. Go to <https://railway.app> and sign in with your **GitHub** account.
2. Click **New Project** -> **Deploy from GitHub repo**.
3. Authorize Railway to see your repos, then pick **TeleframAutoBots**.
4. When asked for a branch, choose **`claude/new-project-planning-215lmq`**
   (or `main` if you've merged the pull request first).
5. Railway auto-detects Python and uses `railway.json` to start the bot. It will
   fail on the first deploy because the environment variables aren't set yet -
   that's expected. Set them in Part 3, then redeploy.

---

## Part 3 - Set environment variables

In your Railway project: **Variables** tab -> add each of these
(**Raw Editor** lets you paste them all at once as `KEY=value` lines):

```
CUSTOMER_BOT_TOKEN=your_customer_bot_token
ADMIN_BOT_TOKEN=your_admin_bot_token
ADMIN_TELEGRAM_ID=your_numeric_id
TELETHON_API_ID=your_api_id
TELETHON_API_HASH=your_api_hash
TELETHON_SESSION_STRING=the_long_string_from_Part_1
SOURCE_CHATS=@ind_crypto
DESTINATION_CHAT_ID=-100xxxxxxxxxx
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODEL=deepseek/deepseek-chat
TIMEZONE=Asia/Kolkata
MORNING_TIME=08:00
NIGHT_TIME=21:00
DB_PATH=/data/app.db
```

Notes:
- **DESTINATION_CHAT_ID**: for a private channel you need the `-100...` number.
  Get it by messaging your channel with the bot added and running `get_id.py`
  in the same Colab notebook, or use a public @username if the channel is public.
- **TIMEZONE**: set to your local zone (e.g. `Asia/Kolkata`) so the morning/night
  jobs fire at your local time.

### Keep your data across restarts (recommended)

Railway's disk resets on every redeploy, which would wipe the SQLite database.
To keep member records:
1. In the service, open **Settings -> Volumes -> New Volume**.
2. Mount it at **`/data`**.
3. Make sure `DB_PATH=/data/app.db` (as above) so the database lives on the
   volume.

Then click **Deploy**. Check the **Deploy Logs** - you want to see
`All services started`.

---

## Verifying it works

- Message **@TeleframAuto_bot** -> it should reply (customer bot + AI working).
- Message **@TeleTradeAdmin_bot** -> it should reply, and `/report` returns stats.
- Post in **@ind_crypto** -> it should appear in your channel (content sync).

## Cost note

Railway's free trial credit is limited and an always-on worker will use it up;
after that you'll need their small hobby tier (~$5/month) to keep it running 24/7.
