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
    get_ipa_read_in_voice_locale,
)
from app.models.lesson import Phrase, Section, SectionType

# (text, voice_id, rate, phoneme mapping, speak locale, enunciation) — none of
# the last three is an attribute of the text, and each changes the audio.
MemoKey = tuple[str, str, str, tuple[tuple[str, str], ...] | None, str | None, Enunciation | None]

# Leading/trailing characters that are not part of a word ("bing?" -> "bing").
_WORD_EDGES = re.compile(r"^\W+|\W+$")


def _bare_word(text: str) -> str:
    return _WORD_EDGES.sub("", text.lower())


@dataclass(frozen=True)
class SynthRequest:
    """One phrase as the TTS is asked for it: the arguments of ``TTSService.synthesize``."""

    text: str
    voice_id: str
    rate: str
    phonemes: Mapping[str, str] | None = None
    speak_locale: str | None = None
    enunciation: Enunciation | None = None

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
        """
        return (
            self.text,
            self.voice_id,
            self.rate,
            tuple(sorted(self.phonemes.items())) if self.phonemes else None,
            self.speak_locale,
            self.enunciation,
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

    def _phrase_phonemes(phrase: Phrase, line: EnunciatedLine | None) -> Mapping[str, str] | None:
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
            return line_phonemes(line, planner) if line_ipa else None
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
        """Per-word IPA for a whole multi-word DRILL step, or ``None``.

        The path above plans a chunk of a word, and a key phrase is not one:
        it arrives with no ``source_word`` and no ``syllable_span``, so the
        whole step used to fall through to plain text. A Cebuano drill
        phrase rendered as plain text was heard as "ilubong uglak" rather
        than "ilubong ugma" (the user's blind A/B, 2026-09-25: "ugma" wrong
        2 of 3 plain, 2 of 2 right once the reading was given).

        Four conditions, each a refusal rather than a guess:

        * a KEY_PHRASES section — dialogue is a full sentence, and phrase IPA
          on one is untested, so it stays plain for every language;
        * a language that asked for this (``get_ipa_for_drill_phrases``) —
          the channel is the adapter's, and another language's Azure voice
          would wrap every word of the step in ``<phoneme>``;
        * a planner that can read a WHOLE word (``plan_word``) — a
          lexicon-backed planner has no such method, and is never asked for
          one, only skipped;
        * at least two words — a lone word is the other path's business, and
          the adapter's own instruction for a single word names a "syllable
          or word", which a phrase must not be asked to say about itself.

        One word the planner cannot read refuses the WHOLE map rather than
        leaving a gap: a half-read phrase is a phrase whose words were
        aligned to the wrong readings, and a wrong reading is worse than the
        plain render this replaces.
        """
        if section.section_type != SectionType.KEY_PHRASES:
            return None
        if not get_ipa_for_drill_phrases(language_code):
            return None
        plan_word = getattr(planner, "plan_word", None)
        if plan_word is None:
            return None
        words = phrase.text.strip(_SENTENCE_PUNCTUATION).split()
        if len(words) < 2:
            return None
        planned = [(word, plan_word(word)) for word in words]
        if any(ipa is None for _, ipa in planned):
            return None
        return {_bare_word(word): ipa for word, ipa in planned}

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

    requests: list[SynthRequest] = []
    for text, phrase, line in zip(texts, section.phrases, lines, strict=True):
        phonemes = _phrase_phonemes(phrase, line)
        requests.append(
            SynthRequest(
                # What goes to the voice as text is the line as written, with
                # none of the stored `` ... `` notation in it.
                text=line.text if line is not None else text,
                voice_id=phrase.voice_id,
                rate=phrase.rate,
                phonemes=phonemes,
                speak_locale=_phrase_locale(phrase, phonemes),
                enunciation=line.words if line is not None else None,
            )
        )
    return requests
