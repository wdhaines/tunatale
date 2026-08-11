"""Keep a verb base card's gloss from contradicting its infinitive front.

The `å lyve` bug: the card front is the infinitive but the back read "is lying",
because the bulk auto-create-from-lesson path takes its gloss from the lesson's
`token_glosses`, which hold the *in-context* meaning of the surface `lyver` in
"Noen lyver" ("Someone is lying"). That gloss is CORRECT for the transcript,
where hovering `lyver` should say "is lying" — so the source map must not
change. Only the card needs the bare dictionary form.

Sibling of `gloss_definiteness.py`, which fixed the same self-contradiction for
noun definiteness (`morder` glossed "the murderer"). This is deliberately NOT
registry-dispatched: the transformation is English-side, so it applies to every
L2 whose verb cards front with a dictionary form — Slovene's `pokazati` → "show"
wants exactly the same treatment.

Direction matters, as it does for definiteness: only ever REMOVE conjugation
framing. An unrecognised form must pass through unchanged, degrading to today's
behaviour rather than to a confidently wrong card.
"""

from __future__ import annotations

import pytest


class TestAlignGlossVerbForm:
    def _align(self, gloss: str) -> str:
        from app.srs.gloss_verb_form import align_gloss_verb_form

        return align_gloss_verb_form(gloss)

    @pytest.mark.parametrize(
        ("gloss", "expected"),
        [
            # The reported bug.
            ("is lying", "lie"),
            # Consonant doubling must be undone: running -> run, not runn.
            ("is running", "run"),
            # Subject pronoun + third-person -s.
            ("he shows", "show"),
            ("she takes", "take"),
            # Leading "to" — generate_word_gloss's prompt bans it, so the
            # aligner must strip it for glosses that arrive with one anyway.
            ("to show", "show"),
            # Silent-e restoration: making -> make, not mak.
            ("is making", "make"),
        ],
    )
    def test_reduces_conjugated_glosses_to_the_bare_form(self, gloss, expected):
        assert self._align(gloss) == expected

    @pytest.mark.parametrize("gloss", ["show", "freeze", "lie", "run"])
    def test_bare_forms_are_untouched(self, gloss):
        """Already-correct glosses must be no-ops.

        `freeze` guards the existing behaviour of row 2998 (`å fryse`), which
        the click path already gets right via generate_word_gloss.
        """
        assert self._align(gloss) == gloss

    def test_handles_each_slash_separated_alternative(self):
        """Glosses arrive as "x / y" as often as a single phrase — mirrors
        align_gloss_definiteness, which splits on the same separator."""
        assert self._align("is lying / is fibbing") == "lie / fib"

    def test_empty_gloss_is_untouched(self):
        assert self._align("") == ""

    def test_unrecognised_form_passes_through_unchanged(self):
        """The safety property: when the rules do not know a form, do nothing.

        A wrong guess mints a card whose back contradicts its front, which is
        the bug this module exists to fix. Passing through merely fails to fix
        it. Those are not equally bad.
        """
        assert self._align("wrought havoc") == "wrought havoc"

    def test_does_not_strip_a_noun_gloss_that_looks_conjugated(self):
        """ "a building" is a noun gloss; reducing it to "build" would be wrong.

        The leading article is the tell that this is not a verb phrase.
        """
        assert self._align("a building") == "a building"


class TestVerbGlossAppliedAtCreation:
    """The wiring, not the helper.

    `_resolve_gloss_translation` is called from BOTH the create path and the
    listen preview, and its call site comments state the reuse is what makes
    "the previewed gloss and the stored gloss identical by construction rather
    than by coincidence". So the alignment must live INSIDE that helper, and the
    upos must be derived inside it too — deriving it separately at each call
    site reintroduces exactly the coincidence that comment warns against.
    """

    def test_verb_lemma_gloss_is_reduced(self):
        from app.api.srs import _resolve_gloss_translation

        out = _resolve_gloss_translation(
            "lyve",
            {"lyver": "is lying", "lyve": "is lying"},
            {"lyver"},
            "lyver",
            language_code="no",
            surface_upos={"lyver": "VERB"},
            warn_on_missing=False,
        )
        assert out == "lie"

    def test_non_verb_gloss_is_left_alone(self):
        """A NOUN whose gloss merely looks verbal must not be reduced."""
        from app.api.srs import _resolve_gloss_translation

        out = _resolve_gloss_translation(
            "bygning",
            {"bygningen": "a building"},
            {"bygningen"},
            "bygningen",
            language_code="no",
            surface_upos={"bygningen": "NOUN"},
            warn_on_missing=False,
        )
        assert out == "a building"

    def test_generator_base_form_is_preferred_over_the_aligner(self):
        """Part A: an LLM-authored base form beats the rules when present.

        The aligner's wordfreq floor provably cannot be perfect (real words and
        corpus noise interleave — see gloss_verb_form._WORD_FLOOR), so where the
        generator supplied a base form, use it verbatim.
        """
        from app.api.srs import _resolve_gloss_translation

        out = _resolve_gloss_translation(
            "svømme",
            {"svømmer": "is swimming"},
            {"svømmer"},
            "svømmer",
            language_code="no",
            surface_upos={"svømmer": "VERB"},
            verb_base_glosses={"svømme": "swim"},
            warn_on_missing=False,
        )
        # The aligner alone cannot reach this: `swim` (1.62e-05) sits below the
        # membership floor, so "is swimming" passes through untouched.
        assert out == "swim"

    def test_falls_back_to_the_aligner_when_the_map_lacks_the_lemma(self):
        """Every lesson generated before Part A has no map — the fallback is
        what repairs them, and it must not be bypassed or removed."""
        from app.api.srs import _resolve_gloss_translation

        out = _resolve_gloss_translation(
            "lyve",
            {"lyver": "is lying"},
            {"lyver"},
            "lyver",
            language_code="no",
            surface_upos={"lyver": "VERB"},
            verb_base_glosses={},
            warn_on_missing=False,
        )
        assert out == "lie"

    def test_map_keyed_for_a_different_verb_also_falls_back(self):
        """A non-empty map that simply has no entry for THIS verb is the same
        situation as no map at all — the lookup misses and the aligner runs."""
        from app.api.srs import _resolve_gloss_translation

        out = _resolve_gloss_translation(
            "lyve",
            {"lyver": "is lying"},
            {"lyver"},
            "lyver",
            language_code="no",
            surface_upos={"lyver": "VERB"},
            verb_base_glosses={"gå": "go"},
            warn_on_missing=False,
        )
        assert out == "lie"

    def test_map_is_ignored_for_a_non_verb_headword(self):
        """The map is verb-only; a NOUN must not be rewritten by a stray key."""
        from app.api.srs import _resolve_gloss_translation

        out = _resolve_gloss_translation(
            "bygning",
            {"bygningen": "a building"},
            {"bygningen"},
            "bygningen",
            language_code="no",
            surface_upos={"bygningen": "NOUN"},
            verb_base_glosses={"bygning": "build"},
            warn_on_missing=False,
        )
        assert out == "a building"

    def test_noun_definiteness_alignment_still_runs(self):
        """Regression guard: adding the verb branch must not displace the noun
        one. `morder` is indefinite, so the leading article still goes."""
        from app.api.srs import _resolve_gloss_translation

        out = _resolve_gloss_translation(
            "morder",
            {"morderen": "the murderer"},
            {"morderen"},
            "morderen",
            language_code="no",
            surface_upos={"morderen": "NOUN"},
            warn_on_missing=False,
        )
        assert out == "murderer"


class TestGeneratorEmitsVerbBaseGlosses:
    """Part A's generation half: the LLM's base form reaches lesson metadata.

    `token_glosses` is NOT changed — it also feeds the transcript, where the
    conjugated gloss is the correct rendering. The base forms go in a separate,
    verb-only map beside it.
    """

    def _story(self, glosses):
        return {
            "title": "Test",
            "scenes": [
                {
                    "label": "At the Café",
                    "lines": [{"speaker": "female-1", "text": "Noen lyver.", "translation": "Someone is lying."}],
                }
            ],
            "dialogue_glosses": glosses,
            "morphology_focus": [],
        }

    def test_verb_base_form_lands_in_its_own_metadata_map(self):
        from app.generation.story import build_lesson_from_story
        from app.languages import get_language

        lesson = build_lesson_from_story(
            self._story([{"word": "lyver", "translation": "is lying", "base": "lie"}]),
            language=get_language("no"),
        )
        meta = lesson.generation_metadata
        assert meta["verb_base_glosses"].get("lyver") == "lie"
        # The transcript's map keeps the in-context gloss, untouched.
        assert meta["token_glosses"]["lyver"] == "is lying"

    def test_entries_without_a_base_contribute_nothing(self):
        """Nouns and any entry the LLM left bare must not enter the map."""
        from app.generation.story import build_lesson_from_story
        from app.languages import get_language

        lesson = build_lesson_from_story(
            self._story([{"word": "noen", "translation": "someone"}]),
            language=get_language("no"),
        )
        assert lesson.generation_metadata["verb_base_glosses"] == {}

    def test_old_lessons_without_the_key_still_load(self):
        """Backward compatibility: metadata written before Part A has no such
        key, and every consumer must treat that as an empty map, not a crash."""
        from app.generation.story import build_lesson_from_story
        from app.languages import get_language

        lesson = build_lesson_from_story(self._story([]), language=get_language("no"))
        assert lesson.generation_metadata.get("verb_base_glosses", {}) == {}
