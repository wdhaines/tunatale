"""LLM-powered translation helper for single terms and short phrases."""

from __future__ import annotations

import re

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
        # NOT parse_gloss_response: this translates terms and short phrases, so
        # the gloss word-cap would truncate legitimate output to "".
        return result.strip()
    except Exception:
        return ""


#: A gloss is a headword's meaning, not prose. Past this many words the reply is
#: the model explaining itself ("I'm sorry, but I cannot determine the meaning of
#: this word.") or translating the whole sentence — neither is recoverable by
#: trimming, and both belong nowhere near the back of a card.
#:
#: Word count, not character count: that refusal is 59 characters and slips under
#: any length cap loose enough to admit a real gloss, but it is 11 words against
#: a gloss's two or three.
#:
#: Six, not eight. A sentence translation of a short dialogue line lands right
#: around eight ("Yes, and the ski track was completely fresh." is exactly 8),
#: and that is the failure mode with no other tell — fluent, correctly
#: punctuated English about the right subject. Six still admits every realistic
#: phrasal gloss ("to put something off until later", "the day before
#: yesterday"). Declining a good gloss costs a fallback or one retry; accepting a
#: sentence translation puts it on a card the learner studies.
_MAX_GLOSS_WORDS = 6

#: Backstop for a single absurd token; the word cap does the real work.
_MAX_GLOSS_CHARS = 80

_GLOSS_LABEL_RE = re.compile(r"^(?:english|meaning|gloss|translation)\s*[:\-]\s*", re.IGNORECASE)


def parse_gloss_response(raw: str) -> str:
    """Clean an LLM gloss reply. ``""`` means "unusable — caller keeps its fallback".

    Every caller of :func:`generate_word_gloss` writes the result straight onto a
    card and guards with ``if gloss:``, so the empty string is the established
    "no opinion" sentinel and rejecting here costs them nothing. Without this the
    prompt's "no quotes, no explanation" was the only thing standing between a
    chatty reply and the back of a flashcard — an instruction, not a guarantee.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    first = _GLOSS_LABEL_RE.sub("", first).strip().strip("\"'`").strip()
    # A one-word reply often arrives with a full stop; that alone is not prose.
    first = first[:-1].strip() if first.endswith(".") else first
    if not first or first.upper() == "NONE":
        return ""
    if len(first) > _MAX_GLOSS_CHARS or len(first.split()) > _MAX_GLOSS_WORDS:
        return ""
    return first


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

    ``pos`` (classla UPOS) is advisory context. Returns "" on LLM failure, and
    also on a reply that is not a gloss — see :func:`parse_gloss_response`.
    Fail-soft either way: card creation must never block on a transient LLM
    error, and every caller guards with ``if gloss:``.
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
        # ...and the sentence when the caller has one, for the same reason. The
        # gloss-retry path (tunatale-1wiw) has nothing BUT the sentence to pick a
        # sense with: the word reached it precisely because the generator's
        # whole-dialogue gloss dropped it, so there is no POS and no gloss to
        # fall back on. Conditional, so the verb re-gloss caller — which passes
        # no sentence — sends the same prompt it always did.
        if sentence:
            prompt += f" — in: {sentence}"
    try:
        result = await client.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            max_tokens=50,
        )
        return parse_gloss_response(result)
    except Exception:
        return ""
