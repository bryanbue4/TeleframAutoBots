import asyncio
import base64
import html
import logging

from aiohttp import web

from app.config import Settings
from app.db import add_route, build_daily_report, list_routes, remove_route

logger = logging.getLogger(__name__)


def _authorized(request: web.Request, password: str) -> bool:
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
    except Exception:
        return False
    _, _, supplied = decoded.partition(":")
    return supplied == password


def _unauthorized() -> web.Response:
    return web.Response(
        status=401,
        headers={"WWW-Authenticate": 'Basic realm="TeleframAutoBots"'},
        text="Authentication required",
    )


def _page(routes: list[dict], stats: dict) -> str:
    rows = "".join(
        f"""
        <tr>
          <td>{r['id']}</td>
          <td>{html.escape(r['source_chat'])}</td>
          <td>{html.escape(r['destination_chat'])}</td>
          <td>
            <form method="post" action="/delete" onsubmit="return confirm('Delete this route?')">
              <input type="hidden" name="id" value="{r['id']}">
              <button class="del">Delete</button>
            </form>
          </td>
        </tr>"""
        for r in routes
    ) or '<tr><td colspan="4" class="empty">No routes yet - add one below.</td></tr>'

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TeleframAutoBots Dashboard</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 0;
         background: #0f1115; color: #e6e6e6; }}
  .wrap {{ max-width: 820px; margin: 0 auto; padding: 24px 16px 64px; }}
  h1 {{ font-size: 1.5rem; }}
  h2 {{ font-size: 1.1rem; margin-top: 32px; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; }}
  .stat {{ background: #1a1d24; border: 1px solid #2a2f3a; border-radius: 10px; padding: 12px; }}
  .stat b {{ display:block; font-size: 1.6rem; }}
  .stat span {{ color:#9aa4b2; font-size:.8rem; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  th, td {{ text-align: left; padding: 10px; border-bottom: 1px solid #2a2f3a; font-size:.95rem; }}
  th {{ color:#9aa4b2; font-weight:600; }}
  .empty {{ color:#9aa4b2; text-align:center; }}
  form.inline {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }}
  input[type=text] {{ flex:1; min-width:160px; padding:10px; border-radius:8px; border:1px solid #2a2f3a;
                      background:#1a1d24; color:#e6e6e6; }}
  button {{ padding:10px 16px; border:0; border-radius:8px; background:#3b82f6; color:#fff; cursor:pointer; }}
  button.del {{ background:#ef4444; padding:6px 12px; }}
  .hint {{ color:#9aa4b2; font-size:.85rem; margin-top:6px; }}
</style></head>
<body><div class="wrap">
  <h1>TeleframAutoBots Dashboard</h1>

  <h2>Today ({html.escape(stats['date'])})</h2>
  <div class="stats">
    <div class="stat"><b>{stats['new_members']}</b><span>New members</span></div>
    <div class="stat"><b>{stats['pending_members']}</b><span>Pending</span></div>
    <div class="stat"><b>{stats['suspicious_members']}</b><span>Suspicious</span></div>
    <div class="stat"><b>{stats['new_user_messages']}</b><span>New-user msgs</span></div>
    <div class="stat"><b>{stats['old_user_messages']}</b><span>Old-user msgs</span></div>
    <div class="stat"><b>{stats['active_chats']}</b><span>Active chats</span></div>
  </div>

  <h2>Content-copy routes</h2>
  <table>
    <tr><th>#</th><th>Source</th><th>Destination</th><th></th></tr>
    {rows}
  </table>

  <h2>Add a route</h2>
  <form class="inline" method="post" action="/add">
    <input type="text" name="source" placeholder="Source e.g. @ind_crypto" required>
    <input type="text" name="destination" placeholder="Destination e.g. -1002146551577 or @mychannel" required>
    <button>Add route</button>
  </form>
  <p class="hint">Source = a channel your account has joined. Destination = a channel you own.
     Changes apply within ~30 seconds.</p>
</div></body></html>"""


def build_dashboard_app(settings: Settings) -> web.Application:
    app = web.Application()

    @web.middleware
    async def auth_mw(request: web.Request, handler):
        if not _authorized(request, settings.dashboard_password):
            return _unauthorized()
        return await handler(request)

    app.middlewares.append(auth_mw)

    async def index(request: web.Request) -> web.Response:
        routes = await list_routes(settings.db_path)
        stats = await build_daily_report(settings.db_path)
        return web.Response(text=_page(routes, stats), content_type="text/html")

    async def add(request: web.Request) -> web.Response:
        data = await request.post()
        source = (data.get("source") or "").strip()
        destination = (data.get("destination") or "").strip()
        if source and destination:
            await add_route(settings.db_path, source, destination)
        raise web.HTTPFound("/")

    async def delete(request: web.Request) -> web.Response:
        data = await request.post()
        try:
            await remove_route(settings.db_path, int(data.get("id")))
        except (TypeError, ValueError):
            pass
        raise web.HTTPFound("/")

    app.router.add_get("/", index)
    app.router.add_post("/add", add)
    app.router.add_post("/delete", delete)
    return app


async def run_dashboard(settings: Settings) -> None:
    """Serve the web dashboard. Disabled unless DASHBOARD_PASSWORD is set, so it
    is never exposed without a password.
    """
    if not settings.dashboard_password:
        logger.warning("DASHBOARD_PASSWORD not set - web dashboard disabled")
        return
    app = build_dashboard_app(settings)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.port)
    await site.start()
    logger.info("Dashboard listening on port %s", settings.port)
    # Keep the task alive alongside the bots.
    while True:
        await asyncio.sleep(3600)
