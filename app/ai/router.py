import logging

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Known-good models tried (in order) if the configured model fails, so a
# mistyped/unavailable OPENROUTER_MODEL can't silently break every AI reply.
FALLBACK_MODELS = ["deepseek/deepseek-chat", "openai/gpt-4o-mini"]


class AIRouter:
    """Thin wrapper around OpenRouter's OpenAI-compatible chat API.

    OpenRouter fronts Anthropic/OpenAI/DeepSeek behind one API key, so model
    choice (and therefore cost/quality trade-off) is just a string per call.
    """

    def __init__(self, api_key: str, default_model: str):
        self.api_key = api_key
        self.default_model = default_model

    async def chat(self, messages: list[dict], model: str | None = None, max_tokens: int = 400) -> str:
        primary = model or self.default_model
        candidates = [primary] + [m for m in FALLBACK_MODELS if m != primary]
        last_error: object = None
        async with httpx.AsyncClient(timeout=30) as client:
            for candidate in candidates:
                try:
                    response = await client.post(
                        OPENROUTER_URL,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"model": candidate, "messages": messages, "max_tokens": max_tokens},
                    )
                    response.raise_for_status()
                    data = response.json()
                    if "choices" in data:
                        return data["choices"][0]["message"]["content"]
                    last_error = data
                    logger.warning("Model %s returned no choices: %s", candidate, data)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.warning("Model %s failed: %s", candidate, exc)
        raise RuntimeError(f"All AI models failed. Last error: {last_error}")

    async def reply_as_customer_assistant(
        self,
        user_message: str,
        history: list[dict] | None = None,
        questions: list[str] | None = None,
        plans: list[str] | None = None,
        scope: str = "",
        instructions: str = "",
    ) -> str:
        business = scope.strip() or "an investment service"
        system = (
            f"You are the AI onboarding assistant for {business}. Your goal is to guide "
            "each customer from first contact through signing up: warmly greet them, "
            "understand what they're looking for, explain the available investment plans, "
            "help them choose one, and collect the details needed to sign them up "
            "(their name, city, a contact number, the plan they want, and how much they "
            "plan to invest). Move one step at a time and keep replies short and clear. "
            "Stay strictly within the scope of our investment services - if the customer "
            "asks about something unrelated, politely steer the conversation back to "
            "helping them invest. Never invent plans or promise returns beyond what is "
            "listed below."
        )
        if plans:
            listed = "\n".join(f"- {p}" for p in plans)
            system += f"\n\nAvailable investment plans (only offer these):\n{listed}"
        if questions:
            joined = "; ".join(questions)
            system += (
                "\n\nAlso, over the course of the conversation, naturally collect answers "
                f"to these questions (one at a time, only when it fits): {joined}."
            )
        if instructions.strip():
            system += f"\n\nAdditional instructions from the business owner (follow these): {instructions.strip()}"
        messages = [
            {"role": "system", "content": system},
            *(history or []),
            {"role": "user", "content": user_message},
        ]
        return await self.chat(messages)

    async def moderate(self, text: str) -> str:
        """Classify a member message as SAFE, ABUSIVE, or SPAM."""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a content moderator for a Telegram community. Classify the "
                    "user's message and reply with EXACTLY one word: SAFE, ABUSIVE, or SPAM. "
                    "ABUSIVE = offensive, abusive, threatening or harassing language. "
                    "SPAM = advertising, scams, promotional links, or repetitive junk. "
                    "Everything else = SAFE."
                ),
            },
            {"role": "user", "content": text},
        ]
        out = (await self.chat(messages, max_tokens=5)).strip().upper()
        for label in ("ABUSIVE", "SPAM", "SAFE"):
            if label in out:
                return label
        return "SAFE"

    async def generate_daily_report_summary(self, stats: dict) -> str:
        messages = [
            {
                "role": "system",
                "content": "You write short, friendly daily operations summaries for a Telegram community owner.",
            },
            {
                "role": "user",
                "content": f"Summarize today's stats in 3-4 sentences:\n{stats}",
            },
        ]
        return await self.chat(messages, max_tokens=200)

    async def generate_greeting_message(self, kind: str) -> str:
        prompt = "good morning" if kind == "morning" else "good night"
        messages = [
            {
                "role": "system",
                "content": "You write a short, warm one or two sentence message for a Telegram community.",
            },
            {"role": "user", "content": f"Write a fresh {prompt} message for the community, friendly and brief."},
        ]
        return await self.chat(messages, max_tokens=100)
