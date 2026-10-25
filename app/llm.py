"""One place for OpenAI chat calls. GPT-5 models reason before answering, so short answers need a
minimal reasoning effort and a sane completion budget; older models ignore the knob."""
from __future__ import annotations

from app import config

_client = None


def client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def chat(prompt: str, max_tokens: int = 200, model: str | None = None) -> str:
    model = model or config.OPENAI_MODEL
    kwargs = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_completion_tokens": max_tokens}
    if model.startswith(("gpt-5", "o")):
        kwargs["reasoning_effort"] = "minimal" if model.startswith("gpt-5") else "low"
    r = client().chat.completions.create(**kwargs)
    return (r.choices[0].message.content or "").strip()
