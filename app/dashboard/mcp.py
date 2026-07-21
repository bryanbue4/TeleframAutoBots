"""Minimal MCP (Model Context Protocol) server over Streamable HTTP.

Exposes bot-management tools so the owner can control the system by chatting
with Claude (added as a custom connector). Protected by a secret token in the
URL path - treat the full /mcp/<token> URL like a password.
"""

import logging

from aiohttp import web

from app.config import Settings
from app.db import (
    add_question,
    add_route,
    build_daily_report,
    end_relay,
    list_active_relays,
    list_flagged_members,
    list_questions,
    list_recent_customers,
    list_routes,
    list_team_members,
    remove_route,
    start_relay,
)

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "get_report",
        "description": "Get today's stats: new/pending/suspicious members, message counts, active chats.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_routes",
        "description": "List content-copy routes (source channel -> destination channel).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_route",
        "description": "Add a content-copy route.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source channel, e.g. @ind_crypto"},
                "destination": {"type": "string", "description": "Destination channel id or @username"},
            },
            "required": ["source", "destination"],
        },
    },
    {
        "name": "delete_route",
        "description": "Delete a content-copy route by its id.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "list_questions",
        "description": "List the intake questions the assistant weaves into customer chats.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_question",
        "description": "Add an intake question for the assistant to ask customers.",
        "inputSchema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "list_customers",
        "description": "List recent customers with message counts and who is handling them (AI or a human).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_flagged",
        "description": "List members flagged as spam/abuse with the reason.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "take_over",
        "description": "Pause the AI for a customer so a human handles them.",
        "inputSchema": {
            "type": "object",
            "properties": {"customer_id": {"type": "integer"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "release",
        "description": "Let the AI resume handling a customer.",
        "inputSchema": {
            "type": "object",
            "properties": {"customer_id": {"type": "integer"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "assign",
        "description": "Hand a customer off to a team member for a live relay.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "integer"},
                "team_id": {"type": "integer"},
            },
            "required": ["customer_id", "team_id"],
        },
    },
    {
        "name": "list_team",
        "description": "List registered team members.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


async def _dispatch(settings: Settings, name: str, args: dict) -> str:
    db = settings.db_path
    if name == "get_report":
        s = await build_daily_report(db)
        return (
            f"Report {s['date']}: new members {s['new_members']}, pending {s['pending_members']}, "
            f"suspicious {s['suspicious_members']}, new-user msgs {s['new_user_messages']}, "
            f"old-user msgs {s['old_user_messages']}, active chats {s['active_chats']}."
        )
    if name == "list_routes":
        routes = await list_routes(db)
        return "\n".join(f"#{r['id']}: {r['source_chat']} -> {r['destination_chat']}" for r in routes) or "No routes."
    if name == "add_route":
        ok = await add_route(db, args["source"], args["destination"])
        return "Route added." if ok else "Route already exists."
    if name == "delete_route":
        ok = await remove_route(db, int(args["id"]))
        return "Deleted." if ok else "No such route."
    if name == "list_questions":
        qs = await list_questions(db)
        return "\n".join(f"#{q['id']}: {q['question']}" for q in qs) or "No questions."
    if name == "add_question":
        await add_question(db, args["question"])
        return "Question added."
    if name == "list_customers":
        cs = await list_recent_customers(db)
        return "\n".join(
            f"#{c['telegram_user_id']} {c['name'] or '-'} | msgs {c['message_count']} | "
            f"{'human' if c['handler'] is not None else 'AI'}"
            for c in cs
        ) or "No customers."
    if name == "list_flagged":
        fs = await list_flagged_members(db)
        return "\n".join(
            f"#{f['telegram_user_id']} {f['name'] or '-'}: {(f['suspicious_reasons'] or '').strip(' ;')}"
            for f in fs
        ) or "No flagged members."
    if name == "take_over":
        await start_relay(db, int(args["customer_id"]), settings.admin_telegram_id)
        return f"AI paused for customer #{args['customer_id']}. Reply from your admin bot with /say."
    if name == "release":
        ok = await end_relay(db, int(args["customer_id"]))
        return "AI resumed." if ok else "No active hold."
    if name == "assign":
        await start_relay(db, int(args["customer_id"]), int(args["team_id"]))
        return f"Customer #{args['customer_id']} assigned to team member {args['team_id']}."
    if name == "list_team":
        team = await list_team_members(db)
        return "\n".join(f"{m['name']} - {m['telegram_id']}" for m in team) or "No team members."
    raise ValueError(f"Unknown tool: {name}")


def make_mcp_handler(settings: Settings):
    async def mcp_handler(request: web.Request) -> web.Response:
        if not settings.mcp_token or request.match_info.get("token") != settings.mcp_token:
            return web.json_response({"error": "unauthorized"}, status=401)
        if request.method == "GET":
            # No server-initiated stream is used; the client posts requests.
            return web.Response(status=405)

        body = await request.json()
        method = body.get("method")
        msg_id = body.get("id")

        if method == "initialize":
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "TeleframAutoBots", "version": "1.0.0"},
                    },
                }
            )
        if method in ("notifications/initialized", "notifications/cancelled"):
            return web.Response(status=202)
        if method == "tools/list":
            return web.json_response({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})
        if method == "tools/call":
            params = body.get("params", {})
            try:
                text = await _dispatch(settings, params["name"], params.get("arguments") or {})
                return web.json_response(
                    {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [{"type": "text", "text": text}]}}
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("MCP tool call failed")
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True},
                    }
                )
        return web.json_response(
            {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Method not found"}}
        )

    return mcp_handler
