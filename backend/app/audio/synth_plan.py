"""What each phrase of a section is sent to the TTS as — decided in ONE place.

Two callers need the answer and must never disagree about it: the renderer,
which sends the requests, and the cost report, which names the same requests to
ask a cache whether each already exists (``app.audio.render_cost``). The report
used to carry its own copy of these rules, and a copy drifts. Measured against
every live lesson on 2026-10-06, by rendering each through the real renderer
with a recording stand-in and comparing: Norwegian agreed on all 11, and the
other two languages did not —

* Tagalog, 57 of a lesson's 227 clips: an IPA-bearing chunk goes out WITHOUT
  its locale wrapper there (``ipa_read_in_voice_locale``), and the copy kept
  the wrapper, so it named files no render writes. It reported 157 clips
  uncached where the renderer would send 100.
* Cebuano, 18 and 14 clips: a multi-word key-phrase step carries a per-word
  reading (``ipa_for_drill_phrases``) and the copy had no such rule. On the
  newer lesson it reported 60 uncached against 46.

Both rules were added to the renderer after the copy was written. So the copy
is gone: both callers call :func:`plan_section`, and
``tests/test_synth_plan_parity.py`` renders through the real renderer and
compares, so a rule added to one side alone cannot pass.

Nothing here does I/O. A request is a value; sending it, deduping it and
deciding whether it is cached all belong to the caller.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.audio.enunciation import ENUNCIATED_SECTIONS, EnunciatedLine, line_phonemes, plan_line
from app.audio.ports import Enunciation
from app.audio.preprocessing.base import TextPreprocessor
from app.generation.section_builder import _SENTENCE_PUNCTUATION
from app.languages import (
    PhonemePlanner,
    get_ipa_for_drill_phrases,
    get_ipa_for_enunciated_lines,
    get_ipa_for_natural_lines,
    get_ipa_read_in_voice_locale,
)
from app.models.lesson import Phrase, Section, SectionType

# (text, voice_id, rate, phoneme mapping, speak locale, enunciation, line,
# stress span) — none of the last four is an attribute of the text, and each
# changes the audio.
MemoKey = tuple[
    str, str, str, tuple[tuple[str, str], ...] | None, str | None, Enunciation | None, bool, tuple[int, int] | None
]

# The sections whose steps are drill steps, read word by word whether a step is
# one word or several (``_drill_phrase_phonemes``); every other section is
# dialogue, whose lines are read as lines or not at all.
_PHRASE_IPA_SECTIONS = frozenset({SectionType.KEY_PHRASES, SectionType.AFFIX_DRILL})

# The sections that say a target-language line whole, at natural speed: the
# Enunciated sections' counterparts (``enunciation.ENUNCIATED_SECTIONS``).
_NATURAL_LINE_SECTIONS = frozenset({SectionType.NATURAL_SPEED, SectionType.TRANSLATED, SectionType.EN_TRANSLATED})

# Leading/trailing characters that are not part of a word ("bing?" -> "bing").
_WORD_EDGES = re.compile(r"^\W+|\W+$")


def _bare_word(text: str) -> str:
    return _WORD_EDGES.sub("", text.lower())


def word_readings(text: str, planner: object | None) -> dict[str, str] | None:
    """The reading of every word of *text*, keyed by bare word, or ``None``.

    For a planner that can read a WHOLE word (``plan_word``); a lexicon-backed
    planner has no such method and gets ``None``. One word with nothing to read
    refuses the whole map, a digit included (its reading is a bare stress mark):
    a half-read phrase aligns words to the wrong readings, and a wrong reading
    is worse than a plain render. Shared by a drill step and a card's word, so
    one word is read one way wherever the voice is told how to say it.
    """
    plan_word = getattr(planner, "plan_word", None)
    if plan_word is None:
        return None
    words = text.strip(_SENTENCE_PUNCTUATION).split()
    if not words:
        return None
    planned = [(word, plan_word(word)) for word in words]
    if any(ipa is None or not ipa.strip("ˈ.") for _, ipa in planned):
        return None
    return {_bare_word(word): ipa for word, ipa in planned}


@dataclass(frozen=True)
class SynthRequest:
    """One phrase as the TTS is asked for it: the arguments of ``TTSService.synthesize``."""

    text: str
    voice_id: str
    rate: str
    phonemes: Mapping[str, str] | None = None
    speak_locale: str | None = None
    enunciation: Enunciation | None = None
    line: bool = False
    # The span of *text* the narrator stresses, in the coordinates of the text
    # the voice actually receives (``plan_section`` re-finds the words there).
    stressed: tuple[int, int] | None = None

    @property
    def key(self) -> MemoKey:
        """The request as a dedupe key: two phrases with the same key are one clip.

        The mapping is PART of the key, not an attribute of the text. Two
        phrases can share (text, voice, rate) and still deserve different
        audio: the same surface string appears as a standalone buildup rung
        (planned) and as a breakdown chunk carrying slicing provenance (never
        planned). Keying on the triple alone lets whichever is submitted first
        serve both — measured on a real lesson, "en" collided six ways and the
        plain render won, so the planned rung silently played un-tagged audio.
        The inverse is worse: a provenance chunk inheriting IPA audio and then
        being sliced.

        The enunciation is part of it for the same reason, and the collision
        it prevents is certain rather than occasional: every Enunciated line
        has the natural-speed section's (text, voice, rate), so whichever
        section was submitted first would voice both. Sorted, so the order a
        mapping was built in cannot split one request into two.

        ``line`` is part of it because it changes what an instruction-led voice
        is told: the same words and readings are one request as a drill phrase
        and another as a line.

        ``stressed`` is part of it for the same reason the enunciation is: the
        same words said with the focus marked and without are two clips, and
        whichever was submitted first would otherwise voice both.
        """
        return (
            self.text,
            self.voice_id,
            self.rate,
            tuple(sorted(self.phonemes.items())) if self.phonemes else None,
            self.speak_locale,
            self.enunciation,
            self.line,
            self.stressed,
        )


def plan_section(
    section: Section,
    language_code: str,
    *,
    preprocessor: TextPreprocessor,
    planner: PhonemePlanner | None,
    locale_for: Callable[[str], str | None],
    slow_word: Callable[[str], str] | None,
) -> list[SynthRequest]:
    """The request for every phrase of *section*, in phrase order.

    Args:
        section: The section being rendered.
        language_code: The lesson's language. A phrase in another language
            (the English beside it) is never planned, cut or given this
            language's locale.
        preprocessor: The language's text preprocessor; what is synthesized is
            the PREPROCESSED text, never ``phrase.text`` itself.
        planner: The language's phoneme planner, or ``None`` for plain text.
        locale_for: Language code -> the SSML locale its text is written in.
            A callable because the two callers hold that differently (a map
            built once per renderer, the registry itself for the report).
        slow_word: The language's long-word cut for an Enunciated line
            (``LanguageConfig.slow_word_fn``), or ``None``.
    """
    texts = [preprocessor.preprocess(phrase.text, section.section_type) for phrase in section.phrases]

    # An Enunciated section says each of its target-language lines one word at
    # a time. Where the words are (and where a long one is cut) is decided
    # HERE, at render time, from the line as stored — so a lesson stored before
    # this existed is said the new way after a re-render. How the pause is made
    # is the adapter's: see ``app.audio.enunciation``.
    enunciated = section.section_type in ENUNCIATED_SECTIONS
    lines: list[EnunciatedLine | None] = [
        plan_line(text, slow_word) if enunciated and phrase.language_code == language_code else None
        for text, phrase in zip(texts, section.phrases, strict=True)
    ]
    line_ipa = get_ipa_for_enunciated_lines(language_code)

    # The same line at natural speed, for a language whose voice is told how it
    # sounds there too. ``plan_line`` is asked only where the words are: it
    # answers ``None`` for a lone word, which is not a line and stays plain,
    # the very clip the Enunciated section plays for it.
    natural_ipa = section.section_type in _NATURAL_LINE_SECTIONS and get_ipa_for_natural_lines(language_code)
    natural_lines: list[EnunciatedLine | None] = [
        plan_line(text) if natural_ipa and phrase.language_code == language_code else None
        for text, phrase in zip(texts, section.phrases, strict=True)
    ]

    def _phrase_phonemes(
        phrase: Phrase, line: EnunciatedLine | None, natural: EnunciatedLine | None
    ) -> Mapping[str, str] | None:
        """Compute phonemes for a sub-word chunk, or None for plain synthesis.

        Stage 2d: sub-word chunks get IPA from the lexicon; whole phrases and
        whole words are the TTS's job.
        """
        if planner is None:
            return None
        if phrase.language_code != language_code:
            return None
        if line is not None:
            # The reading of the whole line, for a language whose voice is
            # told how a line sounds. Never a chunk's: an Enunciated line
            # has no provenance, and for every other language it is plain.
            #
            # Its words are run together, with no mark at their syllable
            # breaks, where every other reading of the language has one
            # (tunatale-ve4p.24). Measured 2026-10-13 on six lines, three
            # fresh takes each way: with the breaks an Enunciated take spoke
            # 1.13 times as long, longer on five lines of six, on a section
            # whose pace the owner had set by ear that day. And by the
            # owner's ear on the paired clips: "baybayon" less chopped up
            # without them, a line "chopped up very weirdly" with them, and
            # an "Oo" with no stop in it with them. This section's word gaps
            # are made after the take, by the adapter's pause rewrite, which
            # also shuts every pause inside a word; a break written into the
            # reading has nothing left to add here and those costs.
            return line_phonemes(line, planner, run_together=True) if line_ipa else None
        if natural is not None:
            return line_phonemes(natural, planner)
        if phrase.source_word is None or phrase.syllable_span is None:
            return _drill_phrase_phonemes(phrase)
        result = planner.plan_chunk(
            phrase.source_word, phrase.syllable_span, upos=phrase.upos or None, chunk_text=phrase.text
        )
        if result is None:
            return None
        # Keyed by the bare word: the adapter looks up word TOKENS, so a
        # chunk stored with its punctuation ("bing?") would otherwise match
        # nothing and silently play as text (tunatale-w4m7.16).
        return {_bare_word(phrase.text): result}

    def _drill_phrase_phonemes(phrase: Phrase) -> Mapping[str, str] | None:
        """Per-word IPA for a whole DRILL step, of one word or several, or ``None``.

        The path above plans a chunk of a word, and a key phrase is not one:
        it arrives with no ``source_word`` and no ``syllable_span``, so the
        whole step used to fall through to plain text. A Cebuano drill
        phrase rendered as plain text was heard as "ilubong uglak" rather
        than "ilubong ugma" (the user's blind A/B, 2026-09-25: "ugma" wrong
        2 of 3 plain, 2 of 2 right once the reading was given).

        A step of ONE word is the same case and was left out of it for a
        while: a root's name, an answer and the whole-word bookends of a
        one-word build-up have no provenance either, and a two-word floor
        here sent them out as plain text, which made the forms an affix drill
        teaches the only steps of it the voice was not told how to say. The
        owner heard "miadpo" for miadto; on fresh takes of adto, moadto and
        miadto plain text was wrong 3 of 9 and the reading 1 of 9 (blind,
        2026-10-13, tunatale-ve4p.21). A reading lowers that rate and does
        not end it. The map then has one entry, which is the shape the
        adapter speaks with its single-word instruction.

        Three conditions, each a refusal rather than a guess:

        * a KEY_PHRASES or AFFIX_DRILL section — a dialogue line is not a
          drill step: where it is read at all it is read as a LINE, by the
          paths above. The affix drill builds its forms and lines up the Key
          Phrases way (tunatale-ve4p.16/.20), so its steps read the same way;
        * a language that asked for this (``get_ipa_for_drill_phrases``) —
          the channel is the adapter's, and another language's Azure voice
          would wrap every word of the step in ``<phoneme>``;
        * a planner that can read a WHOLE word (``plan_word``) — a
          lexicon-backed planner has no such method, and is never asked for
          one, only skipped.

        One word the planner cannot read refuses the WHOLE map rather than
        leaving a gap: a half-read phrase is a phrase whose words were
        aligned to the wrong readings, and a wrong reading is worse than the
        plain render this replaces.
        """
        if section.section_type not in _PHRASE_IPA_SECTIONS:
            return None
        if not get_ipa_for_drill_phrases(language_code):
            return None
        return word_readings(phrase.text, planner)

    # Resolved once per section, and applied only to phrases in the
    # section's own language: a narrator line is English, and declaring it
    # as the target locale would tell Azure to read English text as
    # Norwegian. Same discriminator ``_phrase_phonemes`` already uses.
    target_locale = locale_for(language_code)

    # A locale whose front end ignores IPA would swallow it: an IPA-bearing
    # utterance in such a language is read by the voice's own front end.
    ipa_unwrapped = get_ipa_read_in_voice_locale(language_code)

    def _phrase_locale(phrase: Phrase, phonemes: Mapping[str, str] | None) -> str | None:
        # A phrase in another language (English) declares ITS OWN locale:
        # an it-IT Multilingual voice reading an English translation must
        # be told it is English. For an en-US voice the adapter emits no
        # wrapper and the cache key is unchanged (_lang_locale).
        if phrase.language_code != language_code:
            return locale_for(phrase.language_code)
        if phonemes and ipa_unwrapped:
            return None
        return target_locale

    def _stress_span(phrase: Phrase, request_text: str) -> tuple[int, int] | None:
        """The phrase's stress span, re-based onto the text the voice receives.

        What is synthesized is the PREPROCESSED text — which may differ from
        ``phrase.text`` (an Enunciated line drops its `` ... `` notation) — so
        the stored span cannot simply be copied: the stressed words are re-found
        in what actually goes out. A span whose words are not there is a
        refused, not guessed, because the alternative is a voice stressing
        whatever happens to sit at those coordinates.
        """
        if phrase.stressed is None:
            return None
        a, b = phrase.stressed
        words = phrase.text[a:b]
        start = request_text.find(words)
        if start == -1:
            raise ValueError(f"stressed words {words!r} are not in the text sent to the voice: {request_text!r}")
        return (start, start + len(words))

    requests: list[SynthRequest] = []
    for text, phrase, line, natural in zip(texts, section.phrases, lines, natural_lines, strict=True):
        phonemes = _phrase_phonemes(phrase, line, natural)
        # What goes to the voice as text is the line as written, with none of
        # the stored `` ... `` notation in it.
        request_text = line.text if line is not None else text
        requests.append(
            SynthRequest(
                text=request_text,
                voice_id=phrase.voice_id,
                rate=phrase.rate,
                phonemes=phonemes,
                speak_locale=_phrase_locale(phrase, phonemes),
                enunciation=line.words if line is not None else None,
                # Only with a reading: a line whose reading has a hole in it is
                # sent as plain text, and a plain request is not a line's.
                line=natural is not None and phonemes is not None,
                stressed=_stress_span(phrase, request_text),
            )
        )
    return requests
