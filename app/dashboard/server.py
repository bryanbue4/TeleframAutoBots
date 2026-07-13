import asyncio
import html
import logging
import secrets
import time

import httpx
from aiohttp import web

from app.config import Settings
from app.dashboard.mcp import make_mcp_handler
from app.db import (
    add_question,
    add_route,
    add_team_member,
    add_trusted_device,
    build_daily_report,
    end_relay,
    is_trusted_device,
    list_active_relays,
    list_detected_channels,
    list_flagged_members,
    list_questions,
    list_recent_customers,
    list_routes,
    list_team_members,
    remove_question,
    remove_route,
    remove_team_member,
    set_customer_status,
    start_relay,
)

logger = logging.getLogger(__name__)

OTP_TTL_SECONDS = 300
SESSION_TTL_SECONDS = 3600

# sid -> {"stage": "otp"|"authed", "otp": str, "expiry": float}
_sessions: dict[str, dict] = {}


def _authed(request: web.Request) -> bool:
    sess = _sessions.get(request.cookies.get("sid", ""))
    return bool(sess and sess.get("stage") == "authed" and sess.get("expiry", 0) > time.time())


async def _send_otp(settings: Settings, code: str) -> None:
    url = f"https://api.telegram.org/bot{settings.admin_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            url,
            json={
                "chat_id": settings.admin_telegram_id,
                "text": f"🔐 Dashboard login code: {code}\nExpires in 5 minutes. Ignore if this wasn't you.",
            },
        )


def _shell(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }} * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui,-apple-system,Segoe UI,Roboto,sans-serif; margin:0; background:#0f1115; color:#e6e6e6; }}
  .wrap {{ max-width: 860px; margin: 0 auto; padding: 24px 16px 64px; }}
  h1 {{ font-size:1.5rem; }} h2 {{ font-size:1.05rem; margin-top:30px; }}
  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr)); gap:10px; }}
  .stat {{ background:#1a1d24; border:1px solid #2a2f3a; border-radius:10px; padding:12px; }}
  .stat b {{ display:block; font-size:1.5rem; }} .stat span {{ color:#9aa4b2; font-size:.78rem; }}
  table {{ width:100%; border-collapse:collapse; margin-top:10px; }}
  th,td {{ text-align:left; padding:9px; border-bottom:1px solid #2a2f3a; font-size:.92rem; }}
  th {{ color:#9aa4b2; }} .empty {{ color:#9aa4b2; text-align:center; }}
  form.inline {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }}
  input[type=text],input[type=password] {{ flex:1; min-width:150px; padding:10px; border-radius:8px;
    border:1px solid #2a2f3a; background:#1a1d24; color:#e6e6e6; }}
  button {{ padding:9px 15px; border:0; border-radius:8px; background:#3b82f6; color:#fff; cursor:pointer; }}
  button.del {{ background:#ef4444; padding:5px 11px; }}
  .card {{ max-width:360px; margin:12vh auto 0; background:#1a1d24; border:1px solid #2a2f3a;
    border-radius:14px; padding:24px; }} .err {{ color:#f87171; font-size:.85rem; }}
  .hint {{ color:#9aa4b2; font-size:.82rem; margin-top:6px; }} a {{ color:#60a5fa; }}
</style></head><body><div class="wrap">{body}</div></body></html>"""


def _login_page(error: str = "") -> str:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return _shell(
        "Login",
        f"""<div class="card"><h1>Dashboard login</h1>{err}
        <form method="post" action="/login">
          <p><input type="password" name="password" placeholder="Password" required autofocus></p>
          <p><label><input type="checkbox" name="remember" value="1"> Remember this device (skip the code next time)</label></p>
          <button>Continue</button>
        </form>
        <p class="hint">After the password, a one-time code is sent to your admin bot.</p></div>""",
    )


def _otp_page(error: str = "") -> str:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return _shell(
        "Enter code",
        f"""<div class="card"><h1>Enter code</h1>{err}
        <p class="hint">Check your admin bot on Telegram for a 6-digit code.</p>
        <form method="post" action="/otp">
          <p><input type="text" name="code" placeholder="6-digit code" required autofocus inputmode="numeric"></p>
          <button>Log in</button>
        </form></div>""",
    )


def _rows(items, cols, empty):
    if not items:
        return f'<tr><td colspan="{len(cols) + 1}" class="empty">{empty}</td></tr>'
    out = ""
    for it in items:
        cells = "".join(f"<td>{html.escape(str(it[c]))}</td>" for c in cols)
        out += f"<tr>{cells}<td>{it['_action']}</td></tr>"
    return out


def _del_form(action: str, field: str, value) -> str:
    return (
        f'<form method="post" action="{action}" onsubmit="return confirm(\'Delete?\')">'
        f'<input type="hidden" name="{field}" value="{html.escape(str(value))}">'
        f'<button class="del">Delete</button></form>'
    )


def _post_button(action: str, field: str, value, label: str, css: str) -> str:
    cls = f' class="{css}"' if css else ""
    return (
        f'<form method="post" action="{action}">'
        f'<input type="hidden" name="{field}" value="{html.escape(str(value))}">'
        f'<button{cls}>{html.escape(label)}</button></form>'
    )


def _dashboard_page(stats, routes, questions, team, relays, flagged, customers, channels) -> str:
    route_items = [
        {"id": r["id"], "source": r["source_chat"], "destination": r["destination_chat"],
         "_action": _del_form("/delete", "id", r["id"])}
        for r in routes
    ]
    question_items = [
        {"id": q["id"], "question": q["question"], "_action": _del_form("/delq", "id", q["id"])}
        for q in questions
    ]
    team_items = [
        {"telegram_id": m["telegram_id"], "name": m["name"],
         "_action": _del_form("/delteam", "id", m["telegram_id"])}
        for m in team
    ]
    relay_items = [
        {"customer_id": rl["customer_id"], "team_member_id": rl["team_member_id"],
         "_action": _del_form("/endrelay", "id", rl["customer_id"])}
        for rl in relays
    ]
    flagged_items = [
        {"id": f["telegram_user_id"], "name": f["name"] or "-",
         "reason": (f["suspicious_reasons"] or "").strip(" ;"),
         "_action": _del_form("/removemember", "id", f["telegram_user_id"])}
        for f in flagged
    ]

    def _customer_action(c):
        if c["handler"] is not None:
            return _post_button("/release", "id", c["telegram_user_id"], "Release to AI", "")
        return _post_button("/take", "id", c["telegram_user_id"], "Hold AI (take over)", "")

    customer_items = [
        {"id": c["telegram_user_id"], "name": c["name"] or "-", "msgs": c["message_count"],
         "state": "human" if c["handler"] is not None else "AI", "_action": _customer_action(c)}
        for c in customers
    ]
    channel_items = [
        {"title": ch["title"] or "-", "id": ch["chat_id"], "type": ch["chat_type"], "_action": ""}
        for ch in channels
    ]

    return _shell(
        "Dashboard",
        f"""<h1>TeleframAutoBots Dashboard</h1>
    <p style="text-align:right"><a href="/logout">Log out</a></p>

    <h2>Today ({html.escape(stats['date'])})</h2>
    <div class="stats">
      <div class="stat"><b>{stats['new_members']}</b><span>New members</span></div>
      <div class="stat"><b>{stats['pending_members']}</b><span>Pending</span></div>
      <div class="stat"><b>{stats['suspicious_members']}</b><span>Suspicious</span></div>
      <div class="stat"><b>{stats['new_user_messages']}</b><span>New msgs</span></div>
      <div class="stat"><b>{stats['old_user_messages']}</b><span>Old msgs</span></div>
      <div class="stat"><b>{stats['active_chats']}</b><span>Active chats</span></div>
    </div>

    <h2>Content routes</h2>
    <table><tr><th>#</th><th>Source</th><th>Destination</th><th></th></tr>
      {_rows(route_items, ["id", "source", "destination"], "No routes yet.")}</table>
    <form class="inline" method="post" action="/add">
      <input type="text" name="source" placeholder="Source @channel" required>
      <input type="text" name="destination" placeholder="Destination id/@channel" required>
      <button>Add route</button></form>

    <h2>Detected channels (add the bot to a channel to see its ID)</h2>
    <table><tr><th>Title</th><th>Channel ID</th><th>Type</th><th></th></tr>
      {_rows(channel_items, ["title", "id", "type"], "None yet - add the bot to a channel as admin.")}</table>
    <p class="hint">Copy an ID from here into the Destination field above to route content to it.</p>

    <h2>Intake questions (woven into chats)</h2>
    <table><tr><th>#</th><th>Question</th><th></th></tr>
      {_rows(question_items, ["id", "question"], "No questions yet.")}</table>
    <form class="inline" method="post" action="/addq">
      <input type="text" name="question" placeholder="e.g. What product are you interested in?" required>
      <button>Add question</button></form>

    <h2>Team members</h2>
    <table><tr><th>Telegram ID</th><th>Name</th><th></th></tr>
      {_rows(team_items, ["telegram_id", "name"], "No team members yet.")}</table>
    <form class="inline" method="post" action="/addteam">
      <input type="text" name="telegram_id" placeholder="Telegram ID" required>
      <input type="text" name="name" placeholder="Name" required>
      <button>Add member</button></form>

    <h2>Active handoffs</h2>
    <table><tr><th>Customer</th><th>Team member</th><th></th></tr>
      {_rows(relay_items, ["customer_id", "team_member_id"], "No active handoffs.")}</table>
    <p class="hint">Start a handoff from the admin bot: /assign &lt;customer_id&gt; &lt;team_id&gt;.
      Changes apply within ~30 seconds.</p>

    <h2>⚠️ Flagged members (spam / abuse)</h2>
    <table><tr><th>ID</th><th>Name</th><th>Reason</th><th></th></tr>
      {_rows(flagged_items, ["id", "name", "reason"], "No flagged members.")}</table>

    <h2>Recent customers</h2>
    <table><tr><th>ID</th><th>Name</th><th>Msgs</th><th>Handled by</th><th></th></tr>
      {_rows(customer_items, ["id", "name", "msgs", "state"], "No customers yet.")}</table>
    <p class="hint">"Hold AI" pauses the assistant so you can reply from the admin bot with
      /say &lt;id&gt; &lt;message&gt;. "Release to AI" hands the chat back.</p>""",
    )


def build_dashboard_app(settings: Settings) -> web.Application:
    app = web.Application()

    @web.middleware
    async def auth_mw(request: web.Request, handler):
        # The MCP endpoint authenticates via its secret token in the path, and
        # the login pages must be reachable without a session.
        if request.path in ("/login", "/otp", "/logout") or request.path.startswith("/mcp/"):
            return await handler(request)
        if not _authed(request):
            raise web.HTTPFound("/login")
        return await handler(request)

    app.middlewares.append(auth_mw)

    async def login_get(_):
        return web.Response(text=_login_page(), content_type="text/html")

    async def login_post(request):
        data = await request.post()
        if (data.get("password") or "") != settings.dashboard_password:
            return web.Response(text=_login_page("Wrong password."), content_type="text/html", status=401)
        remember = bool(data.get("remember"))

        # Trusted device -> skip OTP entirely.
        if await is_trusted_device(settings.db_path, request.cookies.get("trust", "")):
            sid = secrets.token_urlsafe(24)
            _sessions[sid] = {"stage": "authed", "expiry": time.time() + SESSION_TTL_SECONDS}
            resp = web.HTTPFound("/")
            resp.set_cookie("sid", sid, httponly=True, max_age=SESSION_TTL_SECONDS, samesite="Lax")
            return resp

        sid = secrets.token_urlsafe(24)
        code = f"{secrets.randbelow(1000000):06d}"
        _sessions[sid] = {
            "stage": "otp",
            "otp": code,
            "expiry": time.time() + OTP_TTL_SECONDS,
            "remember": remember,
        }
        try:
            await _send_otp(settings, code)
        except Exception:
            logger.exception("Failed to send OTP")
            return web.Response(text=_login_page("Could not send code. Try again."), content_type="text/html")
        resp = web.HTTPFound("/otp")
        resp.set_cookie("sid", sid, httponly=True, max_age=SESSION_TTL_SECONDS, samesite="Lax")
        return resp

    async def otp_get(_):
        return web.Response(text=_otp_page(), content_type="text/html")

    async def otp_post(request):
        sess = _sessions.get(request.cookies.get("sid", ""))
        if not sess or sess.get("stage") != "otp":
            raise web.HTTPFound("/login")
        if sess["expiry"] < time.time():
            return web.Response(text=_otp_page("Code expired. Start again at /login."), content_type="text/html")
        data = await request.post()
        if (data.get("code") or "").strip() == sess["otp"]:
            sess["stage"] = "authed"
            sess["expiry"] = time.time() + SESSION_TTL_SECONDS
            resp = web.HTTPFound("/")
            if sess.get("remember"):
                trust = secrets.token_urlsafe(24)
                await add_trusted_device(settings.db_path, trust, days=30)
                resp.set_cookie("trust", trust, httponly=True, max_age=30 * 24 * 3600, samesite="Lax")
            return resp
        return web.Response(text=_otp_page("Wrong code."), content_type="text/html", status=401)

    async def logout(request):
        _sessions.pop(request.cookies.get("sid", ""), None)
        resp = web.HTTPFound("/login")
        resp.del_cookie("sid")
        return resp

    async def index(_):
        return web.Response(
            text=_dashboard_page(
                await build_daily_report(settings.db_path),
                await list_routes(settings.db_path),
                await list_questions(settings.db_path),
                await list_team_members(settings.db_path),
                await list_active_relays(settings.db_path),
                await list_flagged_members(settings.db_path),
                await list_recent_customers(settings.db_path),
                await list_detected_channels(settings.db_path),
            ),
            content_type="text/html",
        )

    async def add(request):
        data = await request.post()
        source, dest = (data.get("source") or "").strip(), (data.get("destination") or "").strip()
        if source and dest:
            await add_route(settings.db_path, source, dest)
        raise web.HTTPFound("/")

    async def delete(request):
        data = await request.post()
        try:
            await remove_route(settings.db_path, int(data.get("id")))
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound("/")

    async def addq(request):
        data = await request.post()
        q = (data.get("question") or "").strip()
        if q:
            await add_question(settings.db_path, q)
        raise web.HTTPFound("/")

    async def delq(request):
        data = await request.post()
        try:
            await remove_question(settings.db_path, int(data.get("id")))
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound("/")

    async def addteam(request):
        data = await request.post()
        try:
            team_id = int((data.get("telegram_id") or "").strip())
        except ValueError:
            raise web.HTTPFound("/")
        name = (data.get("name") or "").strip()
        await add_team_member(settings.db_path, team_id, name)
        raise web.HTTPFound("/")

    async def delteam(request):
        data = await request.post()
        try:
            await remove_team_member(settings.db_path, int(data.get("id")))
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound("/")

    async def endrelay_route(request):
        data = await request.post()
        try:
            await end_relay(settings.db_path, int(data.get("id")))
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound("/")

    async def take(request):
        data = await request.post()
        try:
            await start_relay(settings.db_path, int(data.get("id")), settings.admin_telegram_id)
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound("/")

    async def release(request):
        data = await request.post()
        try:
            await end_relay(settings.db_path, int(data.get("id")))
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound("/")

    async def removemember(request):
        data = await request.post()
        try:
            await set_customer_status(settings.db_path, int(data.get("id")), "removed", reason="dashboard")
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound("/")

    app.router.add_get("/login", login_get)
    app.router.add_post("/login", login_post)
    app.router.add_get("/otp", otp_get)
    app.router.add_post("/otp", otp_post)
    app.router.add_get("/logout", logout)
    app.router.add_get("/", index)
    app.router.add_post("/add", add)
    app.router.add_post("/delete", delete)
    app.router.add_post("/addq", addq)
    app.router.add_post("/delq", delq)
    app.router.add_post("/addteam", addteam)
    app.router.add_post("/delteam", delteam)
    app.router.add_post("/endrelay", endrelay_route)
    app.router.add_post("/take", take)
    app.router.add_post("/release", release)
    app.router.add_post("/removemember", removemember)
    # MCP endpoint for controlling the bot from Claude (custom connector).
    mcp_handler = make_mcp_handler(settings)
    app.router.add_route("*", "/mcp/{token}", mcp_handler)
    return app


async def run_dashboard(settings: Settings) -> None:
    """Serve the web dashboard. Disabled unless DASHBOARD_PASSWORD is set."""
    if not settings.dashboard_password:
        logger.warning("DASHBOARD_PASSWORD not set - web dashboard disabled")
        return
    app = build_dashboard_app(settings)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.port)
    await site.start()
    logger.info("Dashboard listening on port %s", settings.port)
    while True:
        await asyncio.sleep(3600)
