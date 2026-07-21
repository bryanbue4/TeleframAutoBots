"""Dashboard-driven account onboarding: log a new Telegram user account in with
just a phone number + code (no Colab), generate its session string, and save it
as a new account - all from the web UI.

Reuses the primary account's api_id/api_hash by default (those identify the app,
not the user), so onboarding a new account usually needs only phone + code.
"""

import html
import logging
import secrets

from aiohttp import web
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from app.config import Settings
from app.db import add_account

logger = logging.getLogger(__name__)

# token -> {client, phone, form}  (held in memory between the two wizard steps)
_pending: dict[str, dict] = {}


def _shell(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }} * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui,-apple-system,Segoe UI,Roboto,sans-serif; margin:0; background:#0f1115; color:#e6e6e6; }}
  .card {{ max-width: 480px; margin: 6vh auto; background:#1a1d24; border:1px solid #2a2f3a; border-radius:14px; padding:24px; }}
  h1 {{ font-size:1.4rem; }} label {{ display:block; margin:12px 0 4px; font-size:.9rem; color:#cbd5e1; }}
  input {{ width:100%; padding:10px; border-radius:8px; border:1px solid #2a2f3a; background:#0f1115; color:#e6e6e6; }}
  button {{ margin-top:16px; padding:10px 18px; border:0; border-radius:8px; background:#3b82f6; color:#fff; cursor:pointer; font-size:1rem; }}
  .hint {{ color:#9aa4b2; font-size:.82rem; margin-top:8px; }} .err {{ color:#f87171; }}
  a {{ color:#60a5fa; }}
</style></head><body><div class="card">{body}</div></body></html>"""


def _step1(error: str = "", api_id: str = "", api_hash: str = "") -> str:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return _shell(
        "Add account",
        f"""<h1>Add a new Telegram account</h1>{err}
        <form method="post" action="/newaccount/start">
          <label>Account name</label>
          <input name="name" placeholder="e.g. My Second Business" required>
          <label>Customer bot token (from @BotFather)</label>
          <input name="customer_bot_token" placeholder="123456:ABC..." required>
          <label>Admin bot token (from @BotFather)</label>
          <input name="admin_bot_token" placeholder="789012:XYZ..." required>
          <label>Your admin Telegram ID (from @userinfobot)</label>
          <input name="admin_telegram_id" placeholder="123456789" required>
          <label>Telethon API ID</label>
          <input name="api_id" value="{html.escape(api_id)}" required>
          <label>Telethon API hash</label>
          <input name="api_hash" value="{html.escape(api_hash)}" required>
          <label>Phone number of the account (with country code)</label>
          <input name="phone" placeholder="+91..." required>
          <label>Destination channel ID (optional)</label>
          <input name="destination_chat_id" placeholder="-100...">
          <label>Source channels, comma-separated (optional)</label>
          <input name="source_chats" placeholder="@channel1,@channel2">
          <button>Send login code</button>
        </form>
        <p class="hint">A login code will be sent to that phone's Telegram app. API ID/hash are
        pre-filled from your main account - you can reuse them. Get new ones at
        <a href="https://my.telegram.org" target="_blank">my.telegram.org</a> if needed.</p>""",
    )


def _step2(token: str, error: str = "", need_password: bool = False) -> str:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    pw_field = (
        '<label>Two-step verification password</label><input name="password" type="password" required>'
        if need_password
        else ""
    )
    return _shell(
        "Enter code",
        f"""<h1>Enter the login code</h1>{err}
        <p class="hint">Check the Telegram app on that phone for a login code.</p>
        <form method="post" action="/newaccount/verify">
          <input type="hidden" name="token" value="{html.escape(token)}">
          <label>Login code</label>
          <input name="code" inputmode="numeric" placeholder="12345" required autofocus>
          {pw_field}
          <button>Verify & add account</button>
        </form>""",
    )


def register_onboarding(app: web.Application, settings: Settings) -> None:
    async def new_get(_):
        return web.Response(
            text=_step1(api_id=str(settings.telethon_api_id or ""), api_hash=settings.telethon_api_hash or ""),
            content_type="text/html",
        )

    async def start(request):
        data = await request.post()
        form = {k: (data.get(k) or "").strip() for k in (
            "name", "customer_bot_token", "admin_bot_token", "admin_telegram_id",
            "api_id", "api_hash", "phone", "destination_chat_id", "source_chats")}
        if not all(form[k] for k in ("name", "customer_bot_token", "admin_bot_token",
                                     "admin_telegram_id", "api_id", "api_hash", "phone")):
            return web.Response(text=_step1("Please fill in all required fields.",
                                            form.get("api_id", ""), form.get("api_hash", "")),
                                content_type="text/html")
        try:
            client = TelegramClient(StringSession(), int(form["api_id"]), form["api_hash"])
            await client.connect()
            await client.send_code_request(form["phone"])
        except Exception as exc:  # noqa: BLE001
            logger.exception("send_code_request failed")
            return web.Response(text=_step1(f"Could not send code: {exc}",
                                            form.get("api_id", ""), form.get("api_hash", "")),
                                content_type="text/html")
        token = secrets.token_urlsafe(16)
        _pending[token] = {"client": client, "phone": form["phone"], "form": form}
        return web.Response(text=_step2(token), content_type="text/html")

    async def verify(request):
        data = await request.post()
        token = (data.get("token") or "").strip()
        code = (data.get("code") or "").strip()
        password = (data.get("password") or "").strip()
        entry = _pending.get(token)
        if not entry:
            return web.Response(text=_step1("Session expired - start again."), content_type="text/html")
        client = entry["client"]
        try:
            try:
                await client.sign_in(phone=entry["phone"], code=code)
            except SessionPasswordNeededError:
                if not password:
                    return web.Response(text=_step2(token, "This account has 2FA - enter your password.",
                                                    need_password=True), content_type="text/html")
                await client.sign_in(password=password)
            session_string = client.session.save()
            await client.disconnect()
        except Exception as exc:  # noqa: BLE001
            logger.exception("sign_in failed")
            need_pw = "password" in str(exc).lower()
            return web.Response(text=_step2(token, f"Login failed: {exc}", need_password=need_pw),
                                content_type="text/html")

        form = entry["form"]
        try:
            await add_account(settings.db_path, {
                "name": form["name"],
                "customer_bot_token": form["customer_bot_token"],
                "admin_bot_token": form["admin_bot_token"],
                "admin_telegram_id": form["admin_telegram_id"],
                "telethon_api_id": form["api_id"],
                "telethon_api_hash": form["api_hash"],
                "telethon_session_string": session_string,
                "destination_chat_id": form.get("destination_chat_id"),
                "source_chats": form.get("source_chats"),
            })
        except Exception:
            logger.exception("add_account failed")
            return web.Response(text=_step1("Logged in, but saving the account failed. Try again."),
                                content_type="text/html")
        finally:
            _pending.pop(token, None)

        return web.Response(
            text=_shell(
                "Account added",
                """<h1>✅ Account added</h1>
                <p>The new account is saved and will start on the next redeploy
                (Railway → Deployments → Redeploy).</p>
                <p><a href="/">Back to dashboard</a></p>""",
            ),
            content_type="text/html",
        )

    app.router.add_get("/newaccount", new_get)
    app.router.add_post("/newaccount/start", start)
    app.router.add_post("/newaccount/verify", verify)
