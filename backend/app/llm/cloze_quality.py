"""Is a cloze blank DETERMINED by its context? (tunatale-keb0)

``cards.cloze_source.choose_cloze_sentence`` picks the first example sentence
containing the target word and never asks whether the blank has exactly one
right answer. Measured on the live Norwegian deck, over half the clozes fail
that test: every personal-pronoun sentence accepts every pronoun (Norwegian
verbs do not inflect for person), *Er dette ___ bok?* accepts all six
possessives, and ``bak`` and ``utenfor`` are clozed on the very same frame —
*Han venter ___ bygningen* / *Han venter ___ skolen*. Such a card marks a right
answer wrong.

The gate cannot be a word-class rule, because the same classes also produce the
deck's best cards: ``den``/``det`` teach gender agreement, ``seg`` and
``hverandre`` teach reflexivity, and those must survive untouched.

**So the judge is blind.** It hides the answer, shows the model the sentence with
a ``___``, and asks what could fill it. Asking "is this determined?" with the
answer visible invites the model to rationalise a cue after the fact; making it
*fill the blank* measures its actual uncertainty instead. That is why one rule
covers every word class — ``seg`` passes because the model really does produce
only ``seg``, and ``han`` fails because it really does produce all six pronouns.

Everything else in keb0 consumes this: the pre-stage judges a note's own example
before minting, and the ``/review`` "try again" control judges one the learner
has just met. ``ClozeVerdict`` therefore carries the fillers, not just a verdict
— the report and the UI both want to show WHAT ELSE fit.

Fail-soft like :mod:`app.llm.translate`, with one difference that matters: an
LLM failure yields ``"unknown"``, never a verdict. Claiming ``determined``
would silently keep a bad sentence and claiming ``underdetermined`` would burn a
generation call on a good one, so the caller is told nothing was learned and
leaves the sentence alone.

*language* is a display name (``LanguageConfig.name``), resolved by the caller
from the registry exactly as ``generation.prompts`` and ``llm.translate`` do —
this module must carry no language literal of its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.llm.client import LLMClient

#: ``{{c1::han}}`` and ``{{c1::sem::biti, 1sg}}`` alike. A cloze hint never
#: contains ``}``, so one negated class spans both shapes without backtracking.
_CLOZE_MARKER_RE = re.compile(r"\{\{c\d+::[^}]*\}\}")

#: Leading list furniture: ``1.``/``2)``/``-``/``*``/``•``.
_BULLET_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*")

#: Stripped from both ends of a filler. Quotes and sentence punctuation only —
#: never letters.
_TRIM = " \t\"'`.,;:!?…"

#: A filler for a one-word blank is ONE word. Anything longer is the model
#: answering a different question — asked what fits `___ stolen` it offered
#: "Den store", "Den gamle", "Den nye", phrases that CONTAIN the answer and
#: would otherwise each count as a competitor beating it. Two words looked
#: harmlessly permissive and inverted the verdict on a card that was fine.
_MAX_FILLER_WORDS = 1

BLANK = "___"

#: Generous, because a reasoning model spends this budget on reasoning tokens
#: BEFORE it emits a word of the answer. Measured against the live deck at 120:
#: five of six replies came back ``finish_reason=length`` with nothing usable,
#: which `parse_filler_response` correctly reports as "unknown" — a truncated
#: reply is indistinguishable from a refusal, and both mean "learned nothing".
#: The visible answer is a short word list either way, so this cap costs nothing
#: when the model is terse.
_MAX_TOKENS = 1500


@dataclass(frozen=True)
class ClozeVerdict:
    """What the blind fill-in found.

    ``status`` is ``"determined"`` (only the answer fits), ``"underdetermined"``
    (something else fits too, or the answer does not), or ``"unknown"`` (the
    model said nothing usable — not a verdict, and callers must not treat it as
    one).

    ``competitors`` is the subset of *fillers* that are neither the answer nor an
    accepted spelling of it: the reason a card would mark a right answer wrong,
    and the most useful thing to show a human deciding whether to regenerate.
    """

    status: str
    fillers: tuple[str, ...] = ()
    competitors: tuple[str, ...] = field(default=())


def blank_out(sentence: str, surface: str) -> str:
    """Replace the answer in *sentence* with :data:`BLANK`.

    Handles both shapes the codebase stores: a minted cloze arrives already
    marked (``{{c1::han}} kommer i morgen``), while
    ``choose_cloze_sentence`` returns a plain sentence plus the surface it
    matched.

    **Every** occurrence goes, because ``make_cloze_text`` wraps every
    occurrence — leaving a second copy visible would hand the model the answer
    and make the verdict vacuous. Word boundaries are respected for the same
    reason ``choose_cloze_sentence`` respects them: ``for`` must not blank the
    ``for`` inside ``fordi``.
    """
    if not sentence:
        return ""
    blanked = _CLOZE_MARKER_RE.sub(BLANK, sentence)
    if surface.strip():
        blanked = re.sub(rf"\b{re.escape(surface)}\b", BLANK, blanked, flags=re.IGNORECASE)
    return blanked


def parse_filler_response(raw: str) -> tuple[str, ...]:
    """Clean a filler-list reply. ``()`` means "unusable — the caller learns nothing".

    Mirrors :func:`app.llm.translate.parse_gloss_response`: the prompt asks for a
    bare comma-separated list, but an instruction is not a guarantee, and a
    refusal ("I'm sorry, but I cannot determine what fits in this blank.") is
    fluent, correctly punctuated, about the right subject, and would otherwise
    parse as two plausible-looking fillers.

    The discriminator is that a filler is an actual *word*: every token must be
    alphabetic, which drops ``I'm`` and every fragment carrying digits or
    punctuation, while leaving a genuine list untouched. Order is preserved and
    duplicates collapse case-insensitively, first spelling winning.
    """
    text = (raw or "").strip()
    if not text:
        return ()

    items: list[str] = []
    for line in text.splitlines():
        for chunk in line.split(","):
            item = _BULLET_RE.sub("", chunk).strip(_TRIM).strip()
            if not item:
                continue
            tokens = item.split()
            if len(tokens) > _MAX_FILLER_WORDS or not all(t.isalpha() for t in tokens):
                continue
            items.append(item)

    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return tuple(out)


#: ⚠️ "Grammatical", not "plausible", is the load-bearing word here.
#:
#: The first cut asked only what could "naturally and correctly" fill the blank,
#: and the model answered with words that merely fit the TOPIC. Measured on the
#: live deck, that wrongly condemned ``{{c1::Den}} stolen`` — it offered ``min``,
#: ``din``, ``vår``, ``deres`` and ``et``, none of which are grammatical against
#: a definite noun (a possessive there is ``stolen min`` or ``min stol``, and
#: ``et`` is the wrong gender AND indefinite). ``den`` is one of keb0's four
#: must-pass rows precisely BECAUSE agreement constrains it, so a judge that
#: cannot see agreement destroys the cards the feature exists to protect.
_JUDGE_SYSTEM_PROMPT = (
    "You are a native {language} speaker with strict grammar. The user gives you one "
    "{language} sentence with a blank written as ___. List every single word that could "
    "fill that blank so that the whole sentence is completely correct {language}. "
    "Agreement of gender, number and definiteness must hold — do not list a word that is "
    "merely plausible in meaning if the resulting sentence would be ungrammatical. "
    "Order them with the word a native speaker would most naturally use FIRST. "
    "Reply with a comma-separated list of words on one line, with no explanation and no "
    "numbering. If only one word fits, reply with just it."
)


async def judge_cloze(
    client: LLMClient,
    *,
    sentence: str,
    surface: str,
    language: str,
    also_accept: tuple[str, ...] | list[str] = (),
) -> ClozeVerdict:
    """Decide whether *surface* is the only thing that fits its blank in *sentence*.

    *also_accept* are alternate spellings of the same lexical item — the
    caller's registry-resolved ``card_surface_variants``. A front like
    ``mot, imot`` is ONE word wearing two spellings, so the second spelling
    turning up as a filler does not make the blank free.

    The answer is never sent to the model; see the module docstring.
    """
    if not sentence.strip() or not surface.strip():
        return ClozeVerdict(status="unknown")

    blanked = blank_out(sentence, surface)
    if BLANK not in blanked:
        # The surface is not in the sentence, so there is no blank to ask about.
        # Nothing was learned — that is not the same as "the cloze is fine".
        return ClozeVerdict(status="unknown")

    try:
        reply = await client.complete(
            prompt=blanked,
            system_prompt=_JUDGE_SYSTEM_PROMPT.format(language=language),
            temperature=0.0,
            max_tokens=_MAX_TOKENS,
        )
    except Exception:
        return ClozeVerdict(status="unknown")

    fillers = parse_filler_response(reply)
    if not fillers:
        return ClozeVerdict(status="unknown")

    accepted = {surface.casefold(), *(v.casefold() for v in also_accept)}
    competitors = tuple(f for f in fillers if f.casefold() not in accepted)

    # SOLE OCCUPANCY: any competitor at all condemns the cloze.
    #
    # ⚠️ An argmax rule was tried here and is WRONG — recorded because it is the
    # tempting fix. Sole occupancy fails 74-89% of the live deck, which looks
    # like a broken gate, so the rule was relaxed to "the answer is the word the
    # model reaches for first". Measured over all 84 clozes, that passed four of
    # the seven personal pronouns keb0 is ABOUT, including:
    #
    #     han   ___ kommer i morgen    also fits: Hun, Jeg, Vi, Dere, De, Det, …
    #     du    har ___ hentet boka?   also fits: jeg, han, hun, vi, dere, de, man
    #
    # Ten alternatives and a pass. Among equally good answers, which one a blind
    # model lists first is arbitrary, so an argmax measures the coin flip rather
    # than the sentence.
    #
    # The high failure rate is not a bug in the gate, it is the finding: a cloze
    # front carries no gloss and no word class (`DrillCard.svelte`), so the
    # learner really can type any word that fits, and most of this deck's
    # example sentences really do admit several.
    if competitors:
        return ClozeVerdict(status="underdetermined", fillers=fillers, competitors=competitors)
    return ClozeVerdict(status="determined", fillers=fillers)


_GENERATE_SYSTEM_PROMPT = (
    "You write example sentences for {language} flashcards. Given a word, write ONE short "
    "{language} example, at most two sentences, in which that word is the ONLY word that could "
    "appear in its position. Carry a cue that forces it: name the person or thing a pronoun "
    "refers to in a preceding sentence, use a noun whose gender fixes the article, or use a verb "
    "that demands the word. Reply with the example only — no translation, no quotes, no comment."
)


def _strip_preamble(raw: str) -> str:
    """Take the example out of a chatty reply.

    ``Sure! Here is a sentence for you: Hun tar toget.`` is the shape that gets
    past "no comment" in the prompt, and it is recoverable because the preamble
    always ends at a colon. A generated example rarely contains one, and one that
    does loses its first clause — an acceptable trade for not writing "Sure!
    Here is a sentence for you:" onto a card.
    """
    first = next((ln.strip() for ln in (raw or "").splitlines() if ln.strip()), "")
    if ": " in first:
        first = first.split(": ", 1)[1]
    return first.strip().strip("\"'`").strip()


async def generate_cloze_sentence(
    client: LLMClient,
    *,
    word: str,
    gloss: str,
    pos: str,
    language: str,
) -> str | None:
    """Write a sentence whose blank on *word* is determined, or ``None``.

    ``None`` means "no usable sentence" and every caller keeps whatever it had.
    The reply must actually contain *word* on a word boundary — a sentence that
    does not cannot carry its cloze, and the boundary check is the same one that
    stops ``for`` matching ``fordi``.

    The result is a CANDIDATE, not a verdict: callers judge it with
    :func:`judge_cloze` before using it. Generating and trusting would replace a
    sentence measured to be bad with one merely assumed to be good.
    """
    if not word.strip():
        return None

    prompt = f"{word}"
    if gloss:
        prompt += f" ({gloss})"
    if pos:
        prompt += f" [{pos}]"

    try:
        reply = await client.complete(
            prompt=prompt,
            system_prompt=_GENERATE_SYSTEM_PROMPT.format(language=language),
            temperature=0.7,
            max_tokens=_MAX_TOKENS,
        )
    except Exception:
        return None

    sentence = _strip_preamble(reply)
    if not sentence:
        return None
    if not re.search(rf"\b{re.escape(word)}\b", sentence, re.IGNORECASE):
        return None
    return sentence
