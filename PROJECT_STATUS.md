# TeleframAutoBots — Project Status & Resume Guide

A running record of what this project is, everything that's built, how it's
deployed, and how to test/continue. Pick up from here anytime.

Branch: `claude/new-project-planning-215lmq` · Deployed on Railway · Dashboard:
`https://teleframautobots-production.up.railway.app`

---

## What it is
A personal Telegram automation system: an AI-powered customer/investment
onboarding bot, content copying between channels, member moderation, a private
admin bot, a web dashboard, multi-account support, and a Claude MCP connector.

## Architecture (locked-in decisions)
- **Language:** Python (aiogram for bots, Telethon for the user session)
- **Two bots per account:** customer-facing + private admin
- **Reading joined channels:** Telethon user session (Bot API can't read arbitrary channels)
- **Storage:** SQLite, one DB per account (`/data/app.db` primary, `/data/acct_<id>.db` others)
- **Hosting:** Railway (always-on worker), volume mounted at `/data` for persistence
- **AI:** OpenRouter gateway; model `deepseek/deepseek-chat` with automatic fallbacks

## Features built (all live)
- AI customer chat, scoped to a configurable **business scope** + **investment plans** + **intake questions** + free-form **AI instructions**; stays on-topic, guides signup, collects name/city/contact/plan/amount
- **AI moderation:** every message classified SAFE / ABUSIVE / SPAM → admin alert
- **Content copy:** multi-source → multi-destination routes; copies (no "Forwarded from" tag), deduped, paced
- **Member moderation:** join approval, suspicious/duplicate flags, first-contact alerts
- **Human takeover:** reply to a customer notice, or `/take` / `/say` / `/release`; auto AI-resume after 15 min idle
- **Team handoff:** `/addteam`, `/assign` → live 2-way relay; `/done` ends it
- **Daily:** morning/night posts (rotating photos from `assets/`) + AI-summarized reports
- **Web dashboard:** password + OTP-to-admin-bot login, "remember this device"; manages scope, AI instructions, plans, routes, questions, team, flagged members, customers (Hold AI / Release), detected channels; per-account switcher
- **Multi-account:** run several Telegram accounts from one deployment, each isolated; add via dashboard wizard (`/newaccount`, phone + code, no Colab) — new accounts start on next redeploy
- **Claude MCP connector:** control the bot from Claude chat (12 tools) at `/mcp/<MCP_TOKEN>`
- **Auto channel-ID:** add the bot to a channel → it DMs you the ID; also listed in the dashboard

## Admin bot commands
`/report` `/routes` `/addroute` `/delroute` `/questions` `/addquestion` `/delquestion`
`/plans` `/addplan` `/delplan` `/scope` `/setscope` `/setai` `/team` `/addteam` `/delteam`
`/assign` `/endrelay` `/take` `/say` `/release` `/approve` `/reject` `/remove` `/setup`
`/diag` `/help`

## Railway environment variables (names only — set in Railway → Variables)
`CUSTOMER_BOT_TOKEN` `ADMIN_BOT_TOKEN` `ADMIN_TELEGRAM_ID` `TELETHON_API_ID`
`TELETHON_API_HASH` `TELETHON_SESSION_STRING` `SOURCE_CHATS` `DESTINATION_CHAT_ID`
`OPENROUTER_API_KEY` `OPENROUTER_MODEL` (=`deepseek/deepseek-chat`) `TIMEZONE`
`MORNING_TIME` `NIGHT_TIME` `DB_PATH` (=`/data/app.db`) `DASHBOARD_PASSWORD` `PORT`
`MCP_TOKEN`

## How to test
**Dashboard:** open the URL → log in (password → OTP) → set scope/AI-instructions/plans,
add/delete routes & questions, try the account switcher, open the "+ Add a new account"
wizard. Persistence check: set something, redeploy, confirm it's still there.

**Telegram:** message the customer bot (real AI reply, guided investment flow, off-topic
redirect); send a rude/spam message (admin alert); reply to a notice to take over;
`/report` on the admin bot; add the bot to a channel to get its ID DM'd.

## Add a new account (easy, no Colab)
Dashboard → Telegram accounts → **+ Add a new account** → name, 2 new BotFather tokens,
admin ID, phone (API id/hash pre-filled) → send code → enter code → saved → **redeploy** to start it.

## Switch to a new Telegram account/channels
- New channels only: add the bot as admin → get ID → `/addroute` (no redeploy).
- New account: use the dashboard wizard, or update `TELETHON_*` + `ADMIN_TELEGRAM_ID` in Railway.

## Redeploy rule
Code changes auto-deploy on push. Manual redeploy is needed only to **start a newly-added account**.

## Open items / next steps
- Test the dashboard end-to-end, then Telegram flows.
- Phase 2 polish: live account-add without redeploy; per-account MCP tools.
- **Security:** the OpenRouter key and bot tokens were shared in chat during setup —
  rotate them when convenient (OpenRouter → Keys; @BotFather → revoke) and update Railway.

## Key files
- `app/main.py` — starts all accounts + dashboard
- `app/config.py` — settings (strips whitespace from env values)
- `app/accounts.py` — per-account settings + DB path
- `app/db.py` — all SQLite tables/queries
- `app/ai/router.py` — OpenRouter wrapper (fallback models), customer/moderation/report prompts
- `app/customer_bot/bot.py` · `app/admin_bot/bot.py` — the two bots
- `app/content_sync/listener.py` — Telethon copy listener
- `app/scheduler/jobs.py` — morning/night + reports
- `app/dashboard/server.py` · `mcp.py` · `onboarding.py` — web dashboard, MCP, account wizard
