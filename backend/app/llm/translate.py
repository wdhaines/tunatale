"""LLM-powered translation helper for single terms and short phrases."""

from __future__ import annotations

from app.llm.client import LLMClient


async def translate_term(
    client: LLMClient,
    text: str,
    source_lang: str,
    target_lang: str = "en",
) -> str:
    """Return a short translation/gloss for a foreign word or phrase.

    Returns empty string on LLM failure (fail-soft so card creation never fails
    on transient LLM errors).
    """
    system_prompt = (
        f"You are a {source_lang}→{target_lang} translator. "
        "Provide ONLY a short, concise one-line gloss or translation for the given text. "
        "No explanations, no examples, just the translation."
    )
    try:
        result = await client.complete(
            prompt=text,
            system_prompt=system_prompt,
            temperature=0.3,
            max_tokens=50,
        )
        return result.strip()
    except Exception:
        return ""
