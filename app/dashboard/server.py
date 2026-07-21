import asyncio
import html
import logging
import secrets
import time

import httpx
from aiohttp import web

from app.accounts import account_db_path
from app.config import Settings
from app.dashboard.mcp import make_mcp_handler
from app.dashboard.onboarding import register_onboarding
from app.db import (
    add_account,
    add_plan,
    add_question,
    add_route,
    add_team_member,
    add_trusted_device,
    build_daily_report,
    list_accounts,
    remove_account,
    end_relay,
    get_setting,
    init_db,
    is_trusted_device,
    list_active_relays,
    list_detected_channels,
    list_flagged_members,
    list_plans,
    list_questions,
    list_recent_customers,
    list_routes,
    list_team_members,
    remove_plan,
    remove_question,
    remove_route,
    remove_team_member,
    set_customer_status,
    set_setting,
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


def _del_form(action: str, field: str, value, q: str = "") -> str:
    return (
        f'<form method="post" action="{action}{q}" onsubmit="return confirm(\'Delete?\')">'
        f'<input type="hidden" name="{field}" value="{html.escape(str(value))}">'
        f'<button class="del">Delete</button></form>'
    )


def _post_button(action: str, field: str, value, label: str, css: str, q: str = "") -> str:
    action = action + q
    cls = f' class="{css}"' if css else ""
    return (
        f'<form method="post" action="{action}">'
        f'<input type="hidden" name="{field}" value="{html.escape(str(value))}">'
        f'<button{cls}>{html.escape(label)}</button></form>'
    )


def _dashboard_page(stats, routes, questions, team, relays, flagged, customers, channels, plans,
                    scope, accounts, current_account, instructions) -> str:
    q = f"?account={current_account}" if current_account else ""
    route_items = [
        {"id": r["id"], "source": r["source_chat"], "destination": r["destination_chat"],
         "_action": _del_form("/delete", "id", r["id"], q)}
        for r in routes
    ]
    question_items = [
        {"id": qq["id"], "question": qq["question"], "_action": _del_form("/delq", "id", qq["id"], q)}
        for qq in questions
    ]
    plan_items = [
        {"id": p["id"], "plan": p["text"], "_action": _del_form("/delplan", "id", p["id"], q)}
        for p in plans
    ]
    team_items = [
        {"telegram_id": m["telegram_id"], "name": m["name"],
         "_action": _del_form("/delteam", "id", m["telegram_id"], q)}
        for m in team
    ]
    relay_items = [
        {"customer_id": rl["customer_id"], "team_member_id": rl["team_member_id"],
         "_action": _del_form("/endrelay", "id", rl["customer_id"], q)}
        for rl in relays
    ]
    flagged_items = [
        {"id": f["telegram_user_id"], "name": f["name"] or "-",
         "reason": (f["suspicious_reasons"] or "").strip(" ;"),
         "_action": _del_form("/removemember", "id", f["telegram_user_id"], q)}
        for f in flagged
    ]

    def _customer_action(c):
        if c["handler"] is not None:
            return _post_button("/release", "id", c["telegram_user_id"], "Release to AI", "", q)
        return _post_button("/take", "id", c["telegram_user_id"], "Hold AI (take over)", "", q)

    customer_items = [
        {"id": c["telegram_user_id"], "name": c["name"] or "-", "msgs": c["message_count"],
         "state": "human" if c["handler"] is not None else "AI", "_action": _customer_action(c)}
        for c in customers
    ]
    channel_items = [
        {"title": ch["title"] or "-", "id": ch["chat_id"], "type": ch["chat_type"], "_action": ""}
        for ch in channels
    ]
    account_items = [
        {"id": a["id"], "name": a["name"], "admin": a["admin_telegram_id"],
         "destination": a["destination_chat_id"] or "-",
         "_action": (f'<a href="/?account={a["id"]}">Manage</a> ' + _del_form("/delaccount", "id", a["id"]))}
        for a in accounts
    ]

    def _switch(label, aid):
        cur = (aid == current_account)
        href = "/" if aid == 0 else f"/?account={aid}"
        style = ' style="font-weight:bold;text-decoration:underline"' if cur else ""
        return f'<a href="{href}"{style}>{html.escape(label)}</a>'

    switcher = "Managing: " + " · ".join(
        [_switch("Primary", 0)] + [_switch(a["name"], a["id"]) for a in accounts]
    )
    banner = "Primary account" if not current_account else f"Account #{current_account}"

    return _shell(
        "Dashboard",
        f"""<h1>TeleframAutoBots Dashboard</h1>
    <p style="text-align:right"><a href="/logout">Log out</a></p>
    <p class="hint">{switcher}</p>
    <p style="color:#60a5fa"><b>▸ {banner}</b> — settings below apply to this account.</p>

    <h2>Today ({html.escape(stats['date'])})</h2>
    <div class="stats">
      <div class="stat"><b>{stats['new_members']}</b><span>New members</span></div>
      <div class="stat"><b>{stats['pending_members']}</b><span>Pending</span></div>
      <div class="stat"><b>{stats['suspicious_members']}</b><span>Suspicious</span></div>
      <div class="stat"><b>{stats['new_user_messages']}</b><span>New msgs</span></div>
      <div class="stat"><b>{stats['old_user_messages']}</b><span>Old msgs</span></div>
      <div class="stat"><b>{stats['active_chats']}</b><span>Active chats</span></div>
    </div>

    <h2>Business scope (what the AI keeps customers focused on)</h2>
    <form class="inline" method="post" action="/setscope{q}">
      <input type="text" name="scope" placeholder="e.g. We offer crypto investment plans with monthly returns"
        value="{html.escape(scope)}">
      <button>Save scope</button></form>

    <h2>AI instructions (custom rules the assistant follows in every chat)</h2>
    <form class="inline" method="post" action="/setai{q}">
      <input type="text" name="instructions"
        placeholder="e.g. Be formal, always mention 24/7 support, never discuss competitors"
        value="{html.escape(instructions)}">
      <button>Save instructions</button></form>

    <h2>Investment plans (the AI offers these to customers)</h2>
    <table><tr><th>#</th><th>Plan</th><th></th></tr>
      {_rows(plan_items, ["id", "plan"], "No plans yet.")}</table>
    <form class="inline" method="post" action="/addplan{q}">
      <input type="text" name="text" placeholder="e.g. Starter: invest $100, 5% monthly for 6 months" required>
      <button>Add plan</button></form>

    <h2>Content routes</h2>
    <table><tr><th>#</th><th>Source</th><th>Destination</th><th></th></tr>
      {_rows(route_items, ["id", "source", "destination"], "No routes yet.")}</table>
    <form class="inline" method="post" action="/add{q}">
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
    <form class="inline" method="post" action="/addq{q}">
      <input type="text" name="question" placeholder="e.g. What product are you interested in?" required>
      <button>Add question</button></form>

    <h2>Team members</h2>
    <table><tr><th>Telegram ID</th><th>Name</th><th></th></tr>
      {_rows(team_items, ["telegram_id", "name"], "No team members yet.")}</table>
    <form class="inline" method="post" action="/addteam{q}">
      <input type="text" name="telegram_id" placeholder="Telegram ID" required>
      <input type="text" name="name" placeholder="Name" required>
      <button>Add member</button></form>

    <h2>Active handoffs</h2>
    <table><tr><th>Customer</th><th>Team member</th><th></th></tr>
      {_rows(relay_items, ["customer_id", "team_member_id"], "No active handoffs.")}</table>
    <p class="hint">Start a handoff from the admin bot: /assign &lt;customer_id&gt; &lt;team_id&gt;.</p>

    <h2>⚠️ Flagged members (spam / abuse)</h2>
    <table><tr><th>ID</th><th>Name</th><th>Reason</th><th></th></tr>
      {_rows(flagged_items, ["id", "name", "reason"], "No flagged members.")}</table>

    <h2>Recent customers</h2>
    <table><tr><th>ID</th><th>Name</th><th>Msgs</th><th>Handled by</th><th></th></tr>
      {_rows(customer_items, ["id", "name", "msgs", "state"], "No customers yet.")}</table>
    <p class="hint">"Hold AI" pauses the assistant so you can reply from the admin bot with
      /say &lt;id&gt; &lt;message&gt;. "Release to AI" hands the chat back.</p>

    <h2>Telegram accounts (multi-account)</h2>
    <table><tr><th>#</th><th>Name</th><th>Admin ID</th><th>Destination</th><th></th></tr>
      {_rows(account_items, ["id", "name", "admin", "destination"], "Only the primary account is running.")}</table>
    <p class="hint">Click "Manage" to control that account's settings above. New accounts start on the next redeploy.</p>
    <p><a href="/newaccount"><button type="button">+ Add a new account (phone + code, no Colab)</button></a></p>""",
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

    def ctx(request):
        """Resolve which account's database this request targets (query ?account=id).
        Returns (db_path, account_id, redirect_target)."""
        try:
            aid = int(request.query.get("account") or 0)
        except ValueError:
            aid = 0
        if aid > 0:
            return account_db_path(settings.db_path, aid), aid, f"/?account={aid}"
        return settings.db_path, 0, "/"

    async def index(request):
        db, acc, _ = ctx(request)
        await init_db(db)  # ensure schema exists even if this account hasn't started yet
        return web.Response(
            text=_dashboard_page(
                await build_daily_report(db),
                await list_routes(db),
                await list_questions(db),
                await list_team_members(db),
                await list_active_relays(db),
                await list_flagged_members(db),
                await list_recent_customers(db),
                await list_detected_channels(db),
                await list_plans(db),
                await get_setting(db, "business_scope", ""),
                await list_accounts(settings.db_path),
                acc,
                await get_setting(db, "ai_instructions", ""),
            ),
            content_type="text/html",
        )

    async def add(request):
        db, _, target = ctx(request)
        data = await request.post()
        source, dest = (data.get("source") or "").strip(), (data.get("destination") or "").strip()
        if source and dest:
            await add_route(db, source, dest)
        raise web.HTTPFound(target)

    async def delete(request):
        db, _, target = ctx(request)
        data = await request.post()
        try:
            await remove_route(db, int(data.get("id")))
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound(target)

    async def addq(request):
        db, _, target = ctx(request)
        data = await request.post()
        text = (data.get("question") or "").strip()
        if text:
            await add_question(db, text)
        raise web.HTTPFound(target)

    async def delq(request):
        db, _, target = ctx(request)
        data = await request.post()
        try:
            await remove_question(db, int(data.get("id")))
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound(target)

    async def addteam(request):
        db, _, target = ctx(request)
        data = await request.post()
        try:
            team_id = int((data.get("telegram_id") or "").strip())
        except ValueError:
            raise web.HTTPFound(target)
        await add_team_member(db, team_id, (data.get("name") or "").strip())
        raise web.HTTPFound(target)

    async def delteam(request):
        db, _, target = ctx(request)
        data = await request.post()
        try:
            await remove_team_member(db, int(data.get("id")))
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound(target)

    async def endrelay_route(request):
        db, _, target = ctx(request)
        data = await request.post()
        try:
            await end_relay(db, int(data.get("id")))
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound(target)

    async def take(request):
        db, _, target = ctx(request)
        data = await request.post()
        try:
            await start_relay(db, int(data.get("id")), settings.admin_telegram_id)
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound(target)

    async def release(request):
        db, _, target = ctx(request)
        data = await request.post()
        try:
            await end_relay(db, int(data.get("id")))
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound(target)

    async def removemember(request):
        db, _, target = ctx(request)
        data = await request.post()
        try:
            await set_customer_status(db, int(data.get("id")), "removed", reason="dashboard")
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound(target)

    async def addplan(request):
        db, _, target = ctx(request)
        data = await request.post()
        text = (data.get("text") or "").strip()
        if text:
            await add_plan(db, text)
        raise web.HTTPFound(target)

    async def delplan(request):
        db, _, target = ctx(request)
        data = await request.post()
        try:
            await remove_plan(db, int(data.get("id")))
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound(target)

    async def setscope(request):
        db, _, target = ctx(request)
        data = await request.post()
        await set_setting(db, "business_scope", (data.get("scope") or "").strip())
        raise web.HTTPFound(target)

    async def setai(request):
        db, _, target = ctx(request)
        data = await request.post()
        await set_setting(db, "ai_instructions", (data.get("instructions") or "").strip())
        raise web.HTTPFound(target)

    async def addaccount(request):
        data = await request.post()
        required = ("name", "customer_bot_token", "admin_bot_token", "admin_telegram_id")
        if all((data.get(k) or "").strip() for k in required):
            try:
                await add_account(settings.db_path, {k: (data.get(k) or "").strip() for k in (
                    "name", "customer_bot_token", "admin_bot_token", "admin_telegram_id",
                    "telethon_api_id", "telethon_api_hash", "telethon_session_string",
                    "destination_chat_id", "source_chats")})
            except Exception:
                logger.exception("Failed to add account")
        raise web.HTTPFound("/")

    async def delaccount(request):
        data = await request.post()
        try:
            await remove_account(settings.db_path, int(data.get("id")))
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
    app.router.add_post("/addplan", addplan)
    app.router.add_post("/delplan", delplan)
    app.router.add_post("/setscope", setscope)
    app.router.add_post("/setai", setai)
    app.router.add_post("/addaccount", addaccount)
    app.router.add_post("/delaccount", delaccount)
    # Guided account onboarding (phone + code, no Colab).
    register_onboarding(app, settings)
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
