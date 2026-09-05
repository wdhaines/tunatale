"""Prompt builder for curriculum and story generation.

Language-aware: instructions adjust based on the target language.
All prompts request JSON responses for deterministic parsing.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from app.languages import (
    PlannerExample,
    get_language,
    get_morphology_profile,
    get_planner_example,
    get_style_notes,
)
from app.models.language import Language
from app.models.strategy import ContentStrategy, ReviewPressure

# Cap for individual chat messages rendered in the planner prompt (section 5).
# Keeps the prompt within Groq's per-request token budget when pasted Claude
# replies are long prose.
PLANNER_CHAT_MSG_CHARS = 1500

# ── Story system prompt (always applied) ─────────────────────────────────

# {language_style_notes} is replaced by build_story_system_prompt() before
# the remaining {language_name}/{language_code} placeholders are resolved.
SYSTEM_PROMPT = """\
You are an expert {language_name} language instructor creating Pimsleur-style audio lessons.
Your lessons must sound like something a native {language_name} speaker would actually say —
not like a translated textbook. Authenticity and natural idiom are the primary quality bars.

**PEDAGOGICAL PHILOSOPHY**
- Prioritize natural, idiomatic {language_name} over literal English translation equivalents
- Smooth progression: each lesson should feel immediately usable in real conversations
- Consistent characters and social dynamics throughout each lesson
- Use register appropriate to context — service settings (café, shop) call for polite but
  not stilted forms; casual settings call for relaxed, natural speech

**CONTENT QUALITY STANDARDS**
- Total word count: 400–500 words
- Dialogue: 80%+ of content — minimize narrator exposition
- Scenes: 4–6 distinct scenes with English scene labels
- Each scene: 5–12 lines of dialogue (never 2–3 stub exchanges)
- Key phrases: 3–8 practical collocations (female-1 only in KEY_PHRASES section)
- NEVER generate syllable breakdowns — those are added by post-processing
- NEVER use voice numbers higher than 2 (no female-3, male-3)

**LANGUAGE-SPECIFIC AUTHENTICITY RULES**
{language_style_notes}

**VOICE ASSIGNMENT PROTOCOL**
- Use ONLY these 4 L2 voices: female-1, female-2, male-1, male-2
- KEY_PHRASES section: always use female-1 only
- Maintain character-to-voice consistency within each lesson
- Narrator (English descriptions and translations): narrator voice only

**JSON OUTPUT SCHEMA**
Respond with ONLY a JSON object matching this schema (no markdown fences, no preamble):
{{
  "title": "Descriptive lesson title",
  "key_phrases": [
    {{"phrase": "{language_code} phrase", "translation": "English translation"}}
  ],
  "scenes": [
    {{
      "label": "Scene description in English",
      "lines": [
        {{"speaker": "female-1", "text": "{language_code} dialogue line", "translation": "English translation"}}
      ]
    }}
  ],
  "dialogue_glosses": [
    {{"word": "lowercased_word", "translation": "English translation", "base": "bare dictionary form, VERBS ONLY"}}
  ]{morphology_schema}
}}

The "dialogue_glosses" array MUST contain an entry for EVERY unique word that appears
in the dialogue lines — including articles, prepositions, pronouns, auxiliary verbs,
proper names, interjections, and all other words. If a word appears in any dialogue line
in any scene, it must have a gloss entry. No exceptions. Give each word's lowercase form
and a concise English translation. This enables word-level hover translations
in the learning UI.

The "base" key is OPTIONAL and VERBS ONLY: the bare English dictionary form with no
leading "to" (e.g. "show", not "to show" — matching the word-gloss convention), used
when the card fronts the infinitive. Non-verbs and any entry with no dictionary form
must OMIT it entirely.

{morphology_block}

**SCENE HEADER FORMAT**
- All scene labels must be in English, describing location/time/situation
- Example: "At the Riverside Café", "Morning at the Train Station"
- NEVER use standalone L2 scene headers

**TRANSLATION GUIDELINES**
- Provide direct translations only — no cultural commentary
- Keep translations concise and literal
- Translations are for comprehension scaffolding, not style guides
"""


# ── Morphology-tagging block (Slavic case/dual — Slovene only) ────────────
#
# These two fragments are injected into SYSTEM_PROMPT for languages whose
# morphology drills depend on grammatical case + dual number (Slovene). They
# are deliberately omitted for languages that have neither (e.g. Norwegian
# Bokmål), which would otherwise be told to tag cases that don't exist. The
# Slovene fragments reproduce the prior inline text byte-for-byte so the
# Slovene system prompt — and therefore its cassette hashes — stay stable.
#
# Braces are doubled (``{{…}}``) because these strings pass through the final
# ``str.format`` call in build_story_system_prompt alongside the template.

_MORPHOLOGY_SCHEMA_SL = """,
  "morphology_focus": [
    {{"lemma": "lemma", "surface": "inflected_form", "feature": "verb:1sg", "gloss": "English translation"}}
  ]"""

_MORPHOLOGY_BLOCK_SL = """\
Build the "morphology_focus" array LAST by scanning the NATURAL_SPEED lines you wrote and tagging
inflected words ALREADY PRESENT in them. Aim for 4-6 entries, **prioritizing verb conjugations**.

Each entry becomes a fill-in-the-blank drill card: the learner sees the lemma + feature as a hint
and must PRODUCE the inflected surface. **So the surface MUST differ from its dictionary form** —
otherwise the hint gives away the answer and the entry is discarded (wasted slot). This rules out
two things you might otherwise tag:
- **Nominative-singular nouns** (`dan`, `grad`, `hotel`) — the dictionary form IS the nom sg, so
  there's nothing to produce. Do NOT tag `noun:nom:*` unless the surface genuinely differs from the
  lemma (e.g. plurals like `dnevi`, or feminine `hiša`→ still nom so skip). When in doubt, skip nom.
- **Infinitives appearing as-is.**

Therefore favor, in order: (1) **verb conjugations** (sem/si/je/imam/imaš/stane…), (2) **accusative
and locative nouns** whose ending changes the word (`kavo`, `sobo`, `Ljubljani`, `hotelu`),
(3) adjective agreement where the form changes (`lepa`, `lepo`).

- Surface must be copied CHARACTER-FOR-CHARACTER from a NATURAL_SPEED line (same diacritics č/š/ž),
  a SINGLE word, not invented.
- Lemma is the dictionary form (verb infinitive, noun nom sg, adj masc nom sg) and MUST differ from
  the surface — if they are equal, drop the entry.

**Feature strings — use exactly these shapes:**
- `verb:<p><n>` where p ∈ {{1,2,3}} and n ∈ {{sg,du,pl}}. E.g. `verb:1sg`, `verb:3pl`, `verb:1du`.
  Tag every interesting form of biti/imeti/target verbs that varies the person.
- `noun:<case>:<number>` for accusative or locative: `noun:acc:sg`, `noun:loc:pl`. (These are the
  productive noun forms — prefer them over nominative.)
- `noun:nom:<gender>:<number>` ONLY when the nom surface differs from the lemma (e.g. a plural
  `noun:nom:m:pl` `dnevi`). Skip nom singulars whose form equals the dictionary form.
- `adj:nom:<gender>:<number>`: `adj:nom:f:sg`, etc., when the form changes (`lepa`, `lepo`).

**Allowed cases for A1: nom, acc, loc only.** Do NOT emit `noun:gen:*`, `noun:dat:*`, `noun:ins:*`,
or `adj:` with any case other than `nom` — those are A2+ topics that don't belong in A1 drills.

**Cases derive from the governing word, NOT English gloss:** `v/na/pri/o/po` + static location →
`loc` (v Ljubljani); `v/na/čez/skozi` + motion → `acc` (grem v Ljubljano); direct object → `acc`."""


def _morphology_sections(language_code: str) -> tuple[str, str]:
    """Return the (schema fragment, instructions block) for *language_code*.

    Non-empty only for languages whose registry ``morphology_profile`` is
    ``"slavic"`` (the case/dual tagging block); empty strings otherwise (Norwegian,
    Tagalog, …).
    """
    if get_morphology_profile(language_code) == "slavic":
        return (_MORPHOLOGY_SCHEMA_SL, _MORPHOLOGY_BLOCK_SL)
    return ("", "")


def build_story_system_prompt(language: Language) -> str:
    """Build the story system prompt for a given language, including style notes.

    Loads per-language authenticity rules from the plugin registry and injects
    them into the SYSTEM_PROMPT template. Falls back to a generic instruction
    when no style notes exist for the language.
    """
    style_notes = get_style_notes(language.code)
    if not style_notes:
        style_notes = f"Use authentic, natural {language.name} as a native speaker would write and speak."
    morphology_schema, morphology_block = _morphology_sections(language.code)
    # Replace text fragments first (their content may contain literal braces),
    # then resolve the remaining {language_name}/{language_code} placeholders.
    template = SYSTEM_PROMPT.replace("{language_style_notes}", style_notes)
    template = template.replace("{morphology_schema}", morphology_schema)
    template = template.replace("{morphology_block}", morphology_block)
    return template.format(
        language_name=language.name,
        language_code=language.code,
    )


# ── Strategy-specific user prompt templates ───────────────────────────────

_CEFR_BLOCK = """\
**CEFR Level:** {cefr_level}
Calibrate all dialogue to this level:
- A1: Short isolated phrases, present tense, no subordinate clauses
- A2: Simple connected sentences, present/past/near-future, basic connectors (and, but, because)
- B1: Multi-clause sentences, all main tenses, relative clauses, varied connectors
- B2: Complex sentences, nuanced register, conditional mood, idiomatic expressions"""


def _build_cefr_block(cefr_level: str) -> str:
    return _CEFR_BLOCK.format(cefr_level=cefr_level)


STORY_PROMPT_WIDER_TEMPLATE = """\
**Scenario Expansion Language Learning Content Generation Request**

**Language:** {language_name} ({language_code})
**Learning Objective:** {learning_objective}
**Theme/Focus:** {focus}
**Strategy:** WIDER (New Scenarios, Same Difficulty)
**Story Guidance:** {story_guidance}

{cefr_block}

**New Collocations to Teach:**
{new_collocations}

**Review Collocations to Include:**
{review_collocations}

**WIDER STRATEGY RULES**
- Create NEW scenario contexts using familiar vocabulary
- Maintain the SAME difficulty level as prior material
- Introduce maximum 5 new words per scenario to maintain difficulty
- Expand learner's practical application range without increasing complexity
- Reinforce learned patterns in diverse, realistic situations
- Each scene must have 5-12 lines of dialogue
- Use 80%+ dialogue between characters
"""

STORY_PROMPT_DEEPER_TEMPLATE = """\
**DEEPER Strategy Content Generation Request**

**Language:** {language_name} ({language_code})
**Learning Objective:** {learning_objective}
**Theme/Focus:** {focus}
**Strategy:** DEEPER (Enhanced Language Complexity)
**Story Guidance:** {story_guidance}

{cefr_block}

**SOURCE TRANSCRIPT TO ENHANCE:**
```
{source_day_transcript}
```

**New Collocations to Teach:**
{new_collocations}

**Review Collocations to Include:**
{review_collocations}

**DEEPER STRATEGY RULES**
- Enhance language complexity while keeping the same scenarios
- 90%+ L2 dialogue — minimize English usage
- Focus on sophisticated, authentic language patterns
- Each collocation should demonstrate enhanced language complexity
- Each scene must have 5-12 lines of dialogue
"""


# ── The review-vocabulary block ───────────────────────────────────────────

# Unconditional, and deliberately the LAST line of every rendered block so it is
# the most recent instruction the model reads. Theme is what gives way under
# pressure; a scene that stops making sense is a failure at every setting.
COHERENCE_FLOOR = (
    "Whatever you include, the scene must still be one real people could plausibly be "
    "having — never trade coherence to place a word."
)

# The literal every story cassette was recorded with. An empty review set must
# still render exactly this, at every pressure, or sha256(system + user) moves
# and every recorded story replay is silently orphaned.
NO_REVIEW_WORDS = "(none yet)"

_REVIEW_LEAD = "These are the words this learner is closest to forgetting, most urgent first."

# ⚠️ The section HEADER stays in the templates and still reads "Review
# Collocations to Include". Keeping it there is a maintainability call — a
# template whose section is `{review_collocations}` alone can no longer be read
# as a prompt — so the scoping is done in the first line of the body instead,
# which is why NATURAL opens by naming the list "candidates, not requirements".
_REVIEW_INSTRUCTIONS = {
    ReviewPressure.NATURAL: (
        "Treat them as candidates, not requirements: work in the ones that fit the scene "
        "naturally and leave out any that would bend the dialogue out of shape. Including "
        "none of them is a correct answer."
    ),
    ReviewPressure.BALANCED: (
        "Look actively for openings — reshape a line, add a beat, or choose a detail that "
        "gives one of these words a natural home. Leave out only the ones that would still "
        "distort the scene."
    ),
    ReviewPressure.INSISTENT: (
        "Fitting as many of them as possible into the dialogue matters MORE than staying "
        "close to the stated theme — bend the scenario if that is what it takes."
    ),
}


def build_review_block(
    review_words: Sequence[str],
    pressure: ReviewPressure = ReviewPressure.NATURAL,
) -> str:
    """Render the value substituted into a story template's review slot.

    Order is preserved: the selector hands these over most-urgent-first and the
    model should read them that way.
    """
    if not review_words:
        return NO_REVIEW_WORDS
    listed = "\n".join(f"- {word}" for word in review_words)
    return f"{_REVIEW_LEAD}\n{listed}\n{_REVIEW_INSTRUCTIONS[pressure]}\n{COHERENCE_FLOOR}"


STORY_PROMPT_REVIEW_TEMPLATE = """\
**Review Session Content Generation Request**

**Language:** {language_name} ({language_code})
**Strategy:** REVIEW (No Theme — Reinforce Decaying Vocabulary)

{cefr_block}

**Review Collocations to Include:**
{review_collocations}

**REVIEW STRATEGY RULES**
- There is NO theme or scenario to honour. The words above are the content.
- Invent whatever everyday situation gives the most of them a natural home
- Work in as many as you can; leaving one out is a real cost here, unlike in a
  themed lesson
- The result must still be a scene real people could plausibly be having. A list
  of sentences wearing a thin coat of dialogue is a FAILURE, not a compromise —
  with no theme left to trade away, coherence is the only constraint remaining
  and it is not negotiable
- Each scene must have 5-12 lines of dialogue
- Use 80%+ dialogue between characters

**KEY PHRASES FOR A REVIEW SESSION**
Every entry in "key_phrases" must be a multi-word chunk lifted from the dialogue
you wrote — never a bare single word, and never a review word copied back from
the list above. Where a review word is one word on its own, take the phrase
around it in the dialogue that shows it doing its job (a verb with its object, a
connector with the clause it introduces) and use that. A lone word gives the
learner nothing to build up to, which is what the key-phrase drill is for.
"""


def get_strategy_prompt(strategy: ContentStrategy) -> str:
    """Return the user prompt template for the given content strategy."""
    if strategy == ContentStrategy.WIDER:
        return STORY_PROMPT_WIDER_TEMPLATE
    if strategy == ContentStrategy.DEEPER:
        return STORY_PROMPT_DEEPER_TEMPLATE
    if strategy == ContentStrategy.REVIEW:
        return STORY_PROMPT_REVIEW_TEMPLATE
    raise ValueError(f"Unknown strategy: {strategy}")


# ── Planner prompts ───────────────────────────────────────────────────────

PLANNER_SYSTEM_PROMPT_BASE = """\
You are a language curriculum planner helping a learner build a study plan.

Pedagogical approach:
- Apply Krashen's i+1: each day should be just beyond the learner's current level
- Foundation first: build practical vocabulary before introducing complexity
- The Pimsleur 4-section lesson shape (TRANSLATED, KEY_PHRASES, SLOW_SPEED, NATURAL_SPEED) \
is fixed — you decide only the per-day theme, collocations, learning objective, and story guidance

Language of your replies:
- Converse in English. The learner is a beginner and cannot yet read the
  target language fluently.
- In the JSON, write "title", "focus", "learning_objective", and
  "story_guidance" in English. The "collocations" array must contain
  bare target-language phrases only — no English translations,
  parentheses, or glosses of any kind inside the array.
- When you quote a target-language word or phrase in discussion, follow it
  with a brief English gloss in parentheses.

Reply conversationally. When proposing days, include exactly one fenced ```json block of the form:
{"days": [{"day": N, "title": "<English>", "focus": "<English>", \
"collocations": ["<target-language phrase, no gloss>"], \
"learning_objective": "<English>", "story_guidance": "<English>"}]}

When only discussing, include no JSON. 3\u20138 collocations per day."""


_EXAMPLE_ANCHOR = "When only discussing, include no JSON."


def build_planner_system_prompt(code: str) -> str:
    """Planner system prompt for a curriculum whose target language is *code*.

    Splices in one worked example, resolved through the registry so it is always
    in a language OTHER than *code* (``get_planner_example``). The example is
    what teaches the model the output *shape*; showing it in the target language
    causes the model to copy that language into its reply, which is the
    planner-language-contamination class this indirection exists to prevent.

    Falls back to the example-free base when no other language supplies one — a
    same-language example would be worse than none.
    """
    return _render_planner_system_prompt(get_planner_example(code))


def _render_planner_system_prompt(example: PlannerExample | None) -> str:
    """Splice *example* into the base prompt; return the base when it is ``None``.

    Separate from the resolver so the no-example fallback (a single-language
    install) is testable without standing up a registry.
    """
    if example is None:
        return PLANNER_SYSTEM_PROMPT_BASE
    payload = {
        "days": [
            {
                "day": example.day,
                "title": example.title,
                "focus": example.focus,
                "collocations": list(example.collocations),
                "learning_objective": example.learning_objective,
                "story_guidance": example.story_guidance,
            }
        ]
    }
    # ensure_ascii=False keeps non-ASCII letters as characters, matching the
    # hand-written block this replaced; escaping them would change every hash.
    rendered = json.dumps(payload, ensure_ascii=False)
    name = get_language(example.language_code).name
    block = f"Example ({name} curriculum):\n{rendered}\n"
    return PLANNER_SYSTEM_PROMPT_BASE.replace(_EXAMPLE_ANCHOR, block + _EXAMPLE_ANCHOR)


def build_planner_turn_prompt(
    *,
    topic: str,
    cefr_level: str,
    language_name: str,
    language_code: str,
    days: list,
    learner_snapshot: str,
    feedback: list[dict],
    chat: list[dict],
    batch_size: int,
    start_day: int,
) -> str:
    """Build the full user prompt for one planner turn.

    Pure function, fully deterministic: no datetime / randomness / dict-order
    dependence.  All sections are produced in the fixed order specified below.

    PLANNER messages in *chat* are expected to be prose-only
    (``PlannerTurn.reply`` already strips JSON blocks).
    """
    parts: list[str] = []

    # 1  Header
    parts.append(f"Topic: {topic}")
    parts.append(f"CEFR Level: {cefr_level}")
    parts.append(f"Language: {language_name} ({language_code})")
    parts.append("")

    # 2  Committed plan — last 14 full blocks, older as title lines
    parts.append("## Committed Plan")
    parts.append("")

    sorted_days = sorted(days, key=lambda d: d.day)
    if not sorted_days:
        parts.append("(none yet)")
        parts.append("")
    else:
        cutoff = len(sorted_days) - 14
        if cutoff > 0:
            for d in sorted_days[:cutoff]:
                parts.append(f"Day {d.day}: {d.title}")
            parts.append("")
        for d in sorted_days[max(0, cutoff) :]:
            parts.append(f"Day {d.day} \u2014 {d.title}")
            parts.append(f"  Focus: {d.focus}")
            parts.append(f"  Collocations: {', '.join(d.collocations)}")
            parts.append(f"  Learning Objective: {d.learning_objective}")
            parts.append(f"  Story Guidance: {d.story_guidance}")
            parts.append("")

    # 3  Learner snapshot (verbatim)
    parts.append("## Learner Snapshot")
    parts.append("")
    parts.append(learner_snapshot)
    parts.append("")

    # 4  Feedback
    parts.append("## Feedback")
    parts.append("")
    # Filter feedback to only reference days that still exist in the curriculum.
    # A re-import that removed/renumbered days leaves orphaned feedback entries
    # that would inject stale references into every future turn.
    existing_days = {d.day for d in days}
    filtered = [f for f in feedback if f.get("day") in existing_days]
    if not filtered:
        parts.append("(none)")
    else:
        sorted_feedback = sorted(filtered, key=lambda f: f.get("day", 0))
        for entry in sorted_feedback:
            day = entry.get("day", "?")
            note = entry.get("note", "")
            parts.append(f"- Day {day}: {note}")
    parts.append("")

    # 5  Conversation — last 12 messages, older elided
    parts.append("## Conversation")
    parts.append("")
    recent = chat[-12:] if chat else []
    if len(chat) > 12:
        parts.append("(... older messages elided ...)")
    if recent:
        for msg in recent:
            role_label = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            if len(content) > PLANNER_CHAT_MSG_CHARS:
                content = content[:PLANNER_CHAT_MSG_CHARS] + " … [elided]"
            parts.append(f"{role_label}: {content}")
        parts.append("")
    else:
        # Nearly unreachable in the turn path (the current user message is
        # injected into chat before the prompt is built), but keep the section
        # non-blank for symmetry with every other "(none yet)" section.
        parts.append("(none yet)")
        parts.append("")

    # 6  Closing instruction
    parts.append(f"If proposing, propose exactly {batch_size} days starting at day {start_day}.")
    parts.append(
        f"Respond in English \u2014 title, focus, learning_objective, and "
        f"story_guidance in English \u2014 even though the committed plan and "
        f"conversation above are in {language_name}. The collocations array "
        f"is in {language_name}: bare phrases, no English glosses."
    )

    return "\n".join(parts)
