import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class AIRouter:
    """Thin wrapper around OpenRouter's OpenAI-compatible chat API.

    OpenRouter fronts Anthropic/OpenAI/DeepSeek behind one API key, so model
    choice (and therefore cost/quality trade-off) is just a string per call.
    """

    def __init__(self, api_key: str, default_model: str):
        self.api_key = api_key
        self.default_model = default_model

    async def chat(self, messages: list[dict], model: str | None = None, max_tokens: int = 400) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model or self.default_model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    async def reply_as_customer_assistant(
        self,
        user_message: str,
        history: list[dict] | None = None,
        questions: list[str] | None = None,
    ) -> str:
        system = (
            "You are a friendly customer assistant for a private Telegram community. "
            "Greet new members warmly, answer questions helpfully and briefly, and "
            "naturally ask for the member's name and city if not already known. Keep "
            "replies short and conversational."
        )
        if questions:
            joined = "; ".join(questions)
            system += (
                " Over the course of the conversation, naturally and gradually try to "
                f"learn the answers to these questions: {joined}. Ask at most one at a "
                "time, only when it fits the flow - never fire them all at once."
            )
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
