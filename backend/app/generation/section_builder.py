"""Mechanical section builders for Pimsleur-style lessons.

The LLM generates creative content (key phrases + dialogue). These builders
transform that raw data into the four structured Lesson sections deterministically.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.generation.buildup_rules import BuildupRules, reshape_breakdown
from app.generation.syllabify import syllabify_word
from app.languages import get_breakdown_spans, get_prefix_fn
from app.models.breakdown import BreakdownChunk
from app.models.lesson import DRILL_PROMPT_ROLE, Phrase, Section, SectionType

if TYPE_CHECKING:
    # The drill script builds its forms up with this module's breakdown, so the
    # import runs the other way at runtime.
    from app.generation.affix_drill import AffixDrill

logger = logging.getLogger(__name__)

# Type aliases for plain-dict inputs from parsed LLM JSON
KeyPhrase = dict  # {"phrase": str, "translation": str}
DialogueLine = dict  # {"speaker": str, "text": str, "translation": str}
Scene = dict  # {"label": str, "lines": list[DialogueLine]}
Drill = dict  # {"kind", "instruction", "speaker", "answer_speaker", "model": str; "steps": list[dict]}

# Narrator-spoken section titles matching the demo format.
# The "Slow" pass is enunciated speech — each word of the line said apart, no
# rate change (tunatale-v3ri) — and the English gloss sits after/before the L2
# line. The pauses are made at render time (``app.audio.enunciation``), so an
# Enunciated section STORES the same natural line as its natural-speed twin.
SECTION_TITLES: dict[SectionType, str] = {
    SectionType.KEY_PHRASES: "Key Phrases",
    SectionType.NATURAL_SPEED: "Natural Speed",
    SectionType.SLOW_SPEED: "Enunciated",
    SectionType.TRANSLATED: "English After",
    SectionType.SLOW_TRANSLATED: "Enunciated, English After",
    SectionType.EN_TRANSLATED: "English Before",
    SectionType.SLOW_EN_TRANSLATED: "Enunciated, English Before",
    # The player's Drill track: it holds a story's authored drills as well as
    # an affix drill, so it is titled for both (tunatale-h0q3.3).
    SectionType.AFFIX_DRILL: "Drill",
}

# What the narrator says to open an affix drill. Kept apart from the track's
# title above so that renaming the track moved no rendered audio.
AFFIX_DRILL_TITLE = "Affix Drill"

# What the narrator says once before a story's own drills (tunatale-h0q3.3). Not
# in SECTION_TITLES: the section type is still AFFIX_DRILL, which the player's
# Drill phase already plays, but a story's drills are not an affix drill.
STORY_DRILL_TITLE = "Drill"

# The one English line the narrator says at the start of each authored drill.
# A drill may say its own (``instruction``); a transformation drill must, since
# what it changes differs from step to step (Wolff: active to passive, singular
# to plural, statement to negative), so it has no line of its own here.
STORY_DRILL_INSTRUCTIONS: dict[str, str] = {
    "substitution": "Change the sentence.",
    "question_answer": "Answer the question.",
    "true_false": "True or false?",
}
STORY_DRILL_KINDS: tuple[str, ...] = (*STORY_DRILL_INSTRUCTIONS, "transformation")


def _resolve_voice(speaker: str, l2_voice_map: dict[str, str]) -> str:
    voice = l2_voice_map.get(speaker)
    if voice is None:
        raise ValueError(f"speaker {speaker!r} is not in the voice map; known roles: {', '.join(sorted(l2_voice_map))}")
    return voice


def build_word_breakdown(phrase_text: str, language_code: str | None = None) -> list[str]:
    """Build a Pimsleur-style syllable-level backward buildup sequence.

    Delegates to :func:`build_word_breakdown_spans` and discards provenance.
    """
    if language_code is None:
        from app.config import settings

        language_code = settings.target_language
    return [c.text for c in build_word_breakdown_spans(phrase_text, language_code)]


# Sentence punctuation, stripped off each word before it is syllabified.
#
# These chunks are SLICED from one whole-word render of ``source_word``, so a
# word that arrives carrying its sentence punctuation has that punctuation in
# its AUDIO: the learner hears "bong?" and "lubong?" said as questions in the
# middle of a sentence that is not one (tunatale-w4m7.19). The losslessness
# check never caught it, because it compares the fragments against the word
# they were built from and BOTH sides carried the "?" — there was nothing for
# it to disagree with.
#
# The bookends and the multi-word partials keep the phrase exactly as given: a
# partial is the tail of the question and belongs to it.
#
# "'" and "-" are deliberately NOT in the set, and that is the whole subtlety
# of this constant. In Cebuano and Tagalog the hyphen marks a glottal stop
# (kanus-a, pag-anhi) and the apostrophe is a contraction (napulo'g). Both are
# part of the word rather than punctuation on it, and dropping either would
# change what the learner hears, not merely how it is cut up.
_SENTENCE_PUNCTUATION = '?!.,;:…"“”'


def _strip_sentence_punctuation(word: str) -> str:
    """*word* without its leading and trailing sentence punctuation.

    A token that is nothing BUT punctuation is left as it is: there is no word
    under it to render, and returning ``""`` would put an empty chunk into a
    buildup that is spoken aloud.
    """
    stripped = word.strip(_SENTENCE_PUNCTUATION)
    return stripped or word


def _generic_breakdown_spans(phrase: str, words: list[str], language_code: str) -> list[BreakdownChunk]:
    """Generic breakdown with spans for languages without a registered spans function.

    Follows the same right-to-left per-word buildup as the old inline
    ``build_word_breakdown``, but each word's chunks carry provenance so the
    renderer can slice them from a single whole-word render. Single-syllable
    words and multi-word partials get ``None`` provenance; multi-syllable
    words get per-syllable spans guarded by a losslessness check.

    Each word is stripped of its sentence punctuation first
    (:func:`_strip_sentence_punctuation`), and the STRIPPED word is what the
    fragments, the ``source_word`` and the losslessness check all see — the
    render is sliced out of that same string, so a fragment cannot spell the
    word one way and the audio pronounce it another. ``phrase`` is untouched:
    the bookends and the partials speak it as the lesson wrote it.
    """

    def _syls(word: str) -> tuple[list[str], bool]:
        syls = syllabify_word(word, language_code)
        return syls, False if not syls else "".join(syls) == word.lower()

    chunks: list[BreakdownChunk] = [BreakdownChunk(phrase, None, None)]

    if len(words) == 1:
        word = _strip_sentence_punctuation(words[0])
        syls, lossless = _syls(word)
        if len(syls) <= 1:
            chunks.append(BreakdownChunk(phrase, None, None))
            return chunks
        source = word if lossless else None
        n = len(syls)
        for i in range(n - 1, -1, -1):
            chunks.append(BreakdownChunk(syls[i], source, (i, i + 1) if lossless else None))
            # `0 < i`, not just `i < n - 1`: at i == 0 the join IS the whole
            # word, which is the closing rung appended below — emitting both
            # made the buildup say the word twice, back to back. The bookend is
            # the one kept, so the closing rung keeps the phrase's original case
            # and punctuation instead of a lowercased slice.
            if 0 < i < n - 1:
                chunks.append(BreakdownChunk("".join(syls[i:]), source, (i, n) if lossless else None))
        chunks.append(BreakdownChunk(phrase, None, None))
        return chunks

    for word_index in range(len(words) - 1, -1, -1):
        word = _strip_sentence_punctuation(words[word_index])
        syls, lossless = _syls(word)
        source = word if lossless else None
        n = len(syls)

        if n > 1:
            for i in range(n - 1, -1, -1):
                chunks.append(BreakdownChunk(syls[i], source, (i, i + 1) if lossless else None))
                if i < n - 1:
                    chunks.append(BreakdownChunk("".join(syls[i:]), source, (i, n) if lossless else None))
        else:
            # A one-syllable word is its own whole-word span. It is not sliced
            # (nothing to cut), but a planner that voices whole words as IPA
            # (Tagalog's ``ng`` = "nang") is only asked about chunks that say
            # which word they are (tunatale-w4m7.16).
            chunks.append(BreakdownChunk(word, word if lossless else None, (0, 1) if lossless else None))

        if word_index < len(words) - 1:
            partial = " ".join(words[word_index:])
            if partial != phrase:
                chunks.append(BreakdownChunk(partial, None, None))

        # The closing rung, written once — see the single-word branch above. The
        # loop used to append the phrase here and again after it.
        if word_index == 0:
            chunks.append(BreakdownChunk(phrase, None, None))

    return chunks


def build_word_breakdown_spans(phrase_text: str, language_code: str) -> list[BreakdownChunk]:
    """:func:`build_word_breakdown` plus per-chunk slicing provenance.

    The **text sequence is identical** to ``build_word_breakdown``'s — that is
    the invariant this function exists under, because ``app.audio.cues`` builds
    the key-phrases timing manifest from the plain version and any divergence
    would desynchronise every cue.

    Languages with a registered ``breakdown_spans_fn`` call it directly.
    All others use :func:`_generic_breakdown_spans` which generates provenance
    from :func:`syllabify_word` guarded by a losslessness check.
    """
    fn = get_breakdown_spans(language_code)
    if fn is not None:
        return fn(" ".join(phrase_text.strip().split()))
    phrase = " ".join(phrase_text.strip().split())
    words = phrase.split()
    if not words:
        return []
    return _generic_breakdown_spans(phrase, words, language_code)


def build_key_phrases_section(
    key_phrases: list[KeyPhrase],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
    rules: BuildupRules | None = None,
) -> Section:
    """Build the KEY_PHRASES section.

    Emits, in order: one English narrator title phrase, then per key phrase
    1. the L2 phrase (female-1)
    2. the narrator translation (``role="narrator"``)
    3. zero or more L2 word-breakdown steps (female-1)

    A map that names a ``key-phrases`` voice gets that voice for every L2 line
    instead of female-1 (tunatale-w4m7.16: Tagalog's fil-PH voices ignore IPA,
    so its breakdown is spoken by a Multilingual voice that honours it).

    Key phrases with a missing phrase or translation are skipped with a warning,
    so the group count can be lower than ``len(key_phrases)``.

    *rules* keeps each build-up to the words it breaks
    (:mod:`app.generation.buildup_rules`); ``None`` keeps the full build, which
    is what a stored lesson's resync compares against.
    """
    built: set[str] = set()
    prefix_fn = get_prefix_fn(l2_code) if rules is not None else None
    female_1_voice = l2_voice_map.get("key-phrases") or l2_voice_map.get("female-1", narrator_voice)
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.KEY_PHRASES], voice_id=narrator_voice, language_code="en", role="narrator"
        )
    ]

    for kp in key_phrases:
        if not isinstance(kp, dict):
            logger.warning("Skipping non-dict key phrase: %r", kp)
            continue
        phrase_text = kp.get("phrase", "")
        translation = kp.get("translation", "")
        if not phrase_text or not translation:
            logger.warning("Skipping key phrase with missing phrase or translation: %r", kp)
            continue

        phrases.append(Phrase(text=phrase_text, voice_id=female_1_voice, language_code=l2_code))
        phrases.append(Phrase(text=translation, voice_id=narrator_voice, language_code="en", role="narrator"))
        chunks = build_word_breakdown_spans(phrase_text, l2_code)
        if rules is not None:
            chunks = reshape_breakdown(phrase_text, chunks, rules=rules, built=built, prefix_fn=prefix_fn)
        for chunk in chunks:
            phrases.append(
                Phrase(
                    text=chunk.text,
                    voice_id=female_1_voice,
                    language_code=l2_code,
                    source_word=chunk.source_word,
                    syllable_span=chunk.span,
                )
            )

    return Section(section_type=SectionType.KEY_PHRASES, phrases=phrases)


def key_phrase_groups(section: Section, l2_code: str) -> list[range]:
    """The KEY_PHRASES section's key-phrase groups, as phrase-index ranges.

    The structural inverse of :func:`build_key_phrases_section`, and the reason
    that builder is the ONLY place breakdown rules are applied. A section is a
    SNAPSHOT: it was laid out at generation time, under whatever the breakdown
    rules were then. Re-deriving the phrase count from today's rules — as both
    this module's callers used to — assumes those rules never changed. They do,
    and a stored lesson then fails to re-render, after all the TTS is done
    (measured 2026-09-29: 8 of 11 stored Norwegian lessons disagree with today's
    arithmetic).

    So the segmentation is read off the section instead. A group HEAD is a
    phrase whose ``language_code`` is *l2_code* and whose SUCCESSOR has
    ``role == "narrator"``; a group runs from its head up to (not including) the
    next head, or the section end. Phrases before the first head are the title.

    Both halves of that test carry their weight. Breakdown chunks are plain L2
    phrases, so "is L2" alone would make every chunk a head; chunks are never
    followed by a narrator phrase, so the successor test is what keeps them
    inside their group. And the last phrase of a section cannot be a head at
    all, having no successor — which is exactly what makes a stored section with
    one more chunk than today's rules read correctly.

    Returns an empty list for a title-only section. Takes no position on how
    many groups a section SHOULD have; callers compare that to
    ``lesson.key_phrases`` and decide.
    """
    phrases = section.phrases
    # The final phrase is excluded: a head is defined by having a successor.
    heads = [
        i for i in range(len(phrases) - 1) if phrases[i].language_code == l2_code and phrases[i + 1].role == "narrator"
    ]
    bounds = [*heads, len(phrases)]
    # strict=False: the two sides are deliberately offset — N+1 bounds paired as N
    # consecutive groups. strict=True would raise on the final unpaired bound.
    return [range(start, end) for start, end in zip(bounds, bounds[1:], strict=False)]


def build_natural_speed_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
) -> Section:
    """Build the NATURAL_SPEED section with scene labels and multi-speaker dialogue."""
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.NATURAL_SPEED], voice_id=narrator_voice, language_code="en", role="narrator"
        ),
        *_build_translated_phrases(
            scenes,
            l2_voice_map,
            narrator_voice,
            l2_code,
            en_first=False,
            translated=False,
        ),
    ]
    return Section(section_type=SectionType.NATURAL_SPEED, phrases=phrases)


def build_slow_speed_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
) -> Section:
    """Build the SLOW_SPEED section — NATURAL_SPEED's lines, to be enunciated.

    The phrases are the natural lines as written. What makes the section
    Enunciated is its ``section_type``: the renderer cuts each of its
    target-language lines into words and each provider pauses between them
    (``app.audio.enunciation``, tunatale-tyfk). Lessons stored before that hold
    ``Dober ... dan`` here instead, and the renderer reads those too.
    """
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.SLOW_SPEED], voice_id=narrator_voice, language_code="en", role="narrator"
        ),
        *_build_translated_phrases(
            scenes,
            l2_voice_map,
            narrator_voice,
            l2_code,
            en_first=False,
            translated=False,
        ),
    ]
    return Section(section_type=SectionType.SLOW_SPEED, phrases=phrases)


def _build_translated_phrases(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
    *,
    en_first: bool,
    translated: bool = True,
    en_voice_map: dict[str, str] | None = None,
) -> list[Phrase]:
    """Shared scene-loop for the section builders that play a scene's dialogue.

    *en_first*: ``True`` → narrator translation precedes L2 line; ``False`` → L2 first.
    *translated*: ``True`` → each L2 line is paired with its narrator translation,
    and a line with no translation is skipped; ``False`` → the L2 line stands alone
    (NATURAL_SPEED and SLOW_SPEED), so *en_first* has nothing to order and is ignored.
    *en_voice_map*: speaker → the English voice reading that speaker's
    translation; a speaker absent from it (or no map) is read by the narrator.
    The phrase keeps ``role="narrator"`` regardless — see ``Language.tts_en_voice_map``.
    """
    en_voice_map = en_voice_map or {}
    phrases: list[Phrase] = []

    for scene in scenes:
        if not isinstance(scene, dict):
            logger.warning("Skipping non-dict scene: %r", scene)
            continue
        scene_label = scene.get("label", "")
        if not scene_label:
            logger.warning("Skipping scene with missing label: %r", scene)
            continue
        phrases.append(Phrase(text=scene_label, voice_id=narrator_voice, language_code="en", role="narrator"))
        for line in scene.get("lines", []):
            if not isinstance(line, dict):
                logger.warning("Skipping non-dict dialogue line: %r", line)
                continue
            speaker = line.get("speaker", "").lower()
            text = line.get("text", "")
            translation = line.get("translation", "")
            if not speaker or not text or (translated and not translation):
                logger.warning(
                    "Skipping dialogue line with missing speaker, text, or translation: %r"
                    if translated
                    else "Skipping dialogue line with missing speaker or text: %r",
                    line,
                )
                continue
            voice_id = _resolve_voice(speaker, l2_voice_map)
            l2_phrase = Phrase(text=text, voice_id=voice_id, language_code=l2_code, role=speaker)
            if not translated:
                phrases.append(l2_phrase)
                continue
            narrator_phrase = Phrase(
                text=translation,
                voice_id=en_voice_map.get(speaker, narrator_voice),
                language_code="en",
                role="narrator",
            )
            if en_first:
                phrases.append(narrator_phrase)
                phrases.append(l2_phrase)
            else:
                phrases.append(l2_phrase)
                phrases.append(narrator_phrase)

    return phrases


def build_translated_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
    *,
    en_voice_map: dict[str, str] | None = None,
) -> Section:
    """Build the TRANSLATED section — every L2 line followed by narrator translation."""
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.TRANSLATED], voice_id=narrator_voice, language_code="en", role="narrator"
        ),
        *_build_translated_phrases(
            scenes, l2_voice_map, narrator_voice, l2_code, en_first=False, en_voice_map=en_voice_map
        ),
    ]
    return Section(section_type=SectionType.TRANSLATED, phrases=phrases)


def build_slow_translated_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
    *,
    en_voice_map: dict[str, str] | None = None,
) -> Section:
    """Build the SLOW_TRANSLATED section — L2 lines to be enunciated, each with its trailing translation."""
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.SLOW_TRANSLATED],
            voice_id=narrator_voice,
            language_code="en",
            role="narrator",
        ),
        *_build_translated_phrases(
            scenes, l2_voice_map, narrator_voice, l2_code, en_first=False, en_voice_map=en_voice_map
        ),
    ]
    return Section(section_type=SectionType.SLOW_TRANSLATED, phrases=phrases)


def build_en_translated_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
    *,
    en_voice_map: dict[str, str] | None = None,
) -> Section:
    """Build the EN_TRANSLATED section — narrator translation FIRST, then the L2 line."""
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.EN_TRANSLATED],
            voice_id=narrator_voice,
            language_code="en",
            role="narrator",
        ),
        *_build_translated_phrases(
            scenes, l2_voice_map, narrator_voice, l2_code, en_first=True, en_voice_map=en_voice_map
        ),
    ]
    return Section(section_type=SectionType.EN_TRANSLATED, phrases=phrases)


def build_slow_en_translated_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
    *,
    en_voice_map: dict[str, str] | None = None,
) -> Section:
    """Build the SLOW_EN_TRANSLATED section — narrator translation FIRST, then the L2 line to be enunciated."""
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.SLOW_EN_TRANSLATED],
            voice_id=narrator_voice,
            language_code="en",
            role="narrator",
        ),
        *_build_translated_phrases(
            scenes, l2_voice_map, narrator_voice, l2_code, en_first=True, en_voice_map=en_voice_map
        ),
    ]
    return Section(section_type=SectionType.SLOW_EN_TRANSLATED, phrases=phrases)


def build_affix_drill_section(drill: AffixDrill, *, narrator_voice: str, l2_voice: str) -> Section:
    """Build the AFFIX_DRILL section from a drill script (``app.generation.affix_drill``).

    One phrase per step, after the narrator's title. The English is the
    narrator's and every form is *l2_voice*'s: one voice says the forms so that
    what changes between two of them is the affix, not the speaker.

    ``role`` carries what a step IS, because nothing else on a phrase can: a
    prompt (:data:`DRILL_PROMPT_ROLE`) is followed by a pause sized for the
    learner's answer, and the narrator's other English is not. The forms keep
    the script's own names for them, ``model`` and ``answer``.
    """
    phrases: list[Phrase] = [
        Phrase(text=AFFIX_DRILL_TITLE, voice_id=narrator_voice, language_code="en", role="narrator")
    ]
    for step in drill.steps:
        if step.language_code == "en":
            role = DRILL_PROMPT_ROLE if step.kind == "prompt" else "narrator"
            # The step's focus travels with its English: what the narrator is
            # asked to stress is a span of that step's own text, and only the
            # narrator's English is ever stressed (a target-language form keeps
            # the None the dataclass starts with).
            phrases.append(
                Phrase(
                    text=step.text,
                    voice_id=narrator_voice,
                    language_code="en",
                    role=role,
                    stressed=step.stressed,
                )
            )
        else:
            phrases.append(
                Phrase(
                    text=step.text,
                    voice_id=l2_voice,
                    language_code=step.language_code,
                    role=step.kind,
                    source_word=step.source_word,
                    syllable_span=step.span,
                )
            )
    return Section(section_type=SectionType.AFFIX_DRILL, phrases=phrases)


def _default_drill_speaker(scenes: list[Scene]) -> str:
    """The first character in the dialogue, in scene order — narration is not one.

    A drill with no ``speaker`` of its own is given to the dialogue's first
    character so the lesson sounds like the same two people throughout. A story
    with no dialogue at all has no character to borrow, so female-1 speaks.
    """
    for scene in scenes:
        for line in scene.get("lines", []):
            speaker = line.get("speaker", "").lower()
            if speaker not in ("narration", "narrator"):
                return speaker
    return "female-1"


def _answer_speaker(drill: Drill, cue_speaker: str, scenes: list[Scene]) -> str:
    """Who says *drill*'s answers.

    Only a question-and-answer drill has two voices: its own ``answer_speaker``
    if it names one, else the first character in the dialogue who is not the
    one asking. Every other kind is one voice — the cue's — and an
    ``answer_speaker`` on it is ignored.
    """
    if drill["kind"] != "question_answer":
        return cue_speaker
    named = drill.get("answer_speaker")
    if named:
        return named.lower()
    for scene in scenes:
        for line in scene.get("lines", []):
            speaker = line.get("speaker", "").lower()
            if speaker not in ("narration", "narrator") and speaker != cue_speaker:
                return speaker
    return cue_speaker


def build_story_drill_section(
    drills: list[Drill],
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
) -> Section:
    """Build the AFFIX_DRILL section a story's own ``drills`` become (tunatale-h0q3.3).

    The section type stays ``AFFIX_DRILL`` on purpose: that is the player's
    Drill phase and the hands-free run's, and neither needs to learn a second
    kind of drill to play these.

    The narrator opens with :data:`STORY_DRILL_TITLE`, then says one short
    English line before each drill (:data:`STORY_DRILL_INSTRUCTIONS`). A
    substitution drill models its sentence before asking for the changes; every
    drill then gives each step's cue and its answer back to back, which is the
    shape the pause calculator and the cues already understand.

    Voices come from the drill or from the dialogue: the cue (and the model) is
    the drill's ``speaker``, else the dialogue's first character
    (:func:`_default_drill_speaker`); the answer is :func:`_answer_speaker`'s
    call. Every role is resolved through :func:`_resolve_voice`, so a role no
    voice map names is refused here rather than at render time.
    """
    default_speaker = _default_drill_speaker(scenes)
    phrases: list[Phrase] = [
        Phrase(text=STORY_DRILL_TITLE, voice_id=narrator_voice, language_code="en", role="narrator")
    ]
    for drill in drills:
        kind = drill["kind"]
        instruction = drill.get("instruction") or STORY_DRILL_INSTRUCTIONS[kind]
        phrases.append(Phrase(text=instruction, voice_id=narrator_voice, language_code="en", role="narrator"))
        # Lower-cased, as validation and the dialogue's own lines read a role.
        cue_speaker = (drill.get("speaker") or default_speaker).lower()
        cue_voice = _resolve_voice(cue_speaker, l2_voice_map)
        answer_voice = _resolve_voice(_answer_speaker(drill, cue_speaker, scenes), l2_voice_map)
        if kind == "substitution":
            phrases.append(Phrase(text=drill["model"], voice_id=cue_voice, language_code=l2_code, role="model"))
        for step in drill["steps"]:
            phrases.append(Phrase(text=step["cue"], voice_id=cue_voice, language_code=l2_code, role=DRILL_PROMPT_ROLE))
            phrases.append(Phrase(text=step["answer"], voice_id=answer_voice, language_code=l2_code, role="answer"))
    return Section(section_type=SectionType.AFFIX_DRILL, phrases=phrases)
