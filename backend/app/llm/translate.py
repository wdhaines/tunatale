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


async def generate_word_gloss(
    client: LLMClient,
    *,
    surface: str,
    lemma: str,
    source_lang: str,
    pos: str = "",
    feature: str = "",
    sentence: str = "",
    target_lang: str = "en",
) -> str:
    """Return a concise English gloss for a word, aware of part of speech.

    Two modes:
    - With a morphology *feature* (an inflection cloze, e.g. a biti conjugation):
      gloss the specific inflected form, reflecting person/number/tense
      (``boste`` / ``verb:2pl`` → "you will be").
    - Without a feature (a base card): a bare dictionary gloss of the *lemma*;
      verbs use the bare form with no leading "to" ("pokazati" → "show"), to
      match the existing verb cards.

    ``pos`` (classla UPOS) is advisory context. Returns "" on LLM failure
    (fail-soft — card creation must never block on a transient LLM error).
    """
    if feature:
        system_prompt = (
            f"You are a {source_lang}->{target_lang} translator. "
            f"Give ONLY a concise English gloss for the {source_lang} word form as it functions "
            "grammatically, reflecting person, number and tense for verb forms "
            "(e.g. 'you will be', 'I am not'). No quotes, no explanation, no trailing period."
        )
        prompt = f"{surface} ({feature})"
        if sentence:
            prompt += f" — in: {sentence}"
    else:
        system_prompt = (
            f"You are a {source_lang}->{target_lang} translator. "
            "Give ONLY a concise dictionary gloss (one to three words) for the given "
            f"{source_lang} word. For verbs, use the bare form WITHOUT a leading 'to' "
            "(e.g. 'show', not 'to show'). No quotes, no explanation, no trailing period."
        )
        # Include the part of speech so an ambiguous lemma is glossed in the
        # right sense — e.g. "hotel" (NOUN) must not come back as the verb "to
        # want" (backlog 10). Mirrors the feature branch's "{surface} ({feature})".
        prompt = f"{lemma} ({pos})" if pos else lemma
    try:
        result = await client.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            max_tokens=50,
        )
        return result.strip()
    except Exception:
        return ""
