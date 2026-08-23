"""Notetype field-role profiles.

A :class:`NotetypeProfile` maps semantic roles (the L2 word, the English gloss,
the disambiguation key) to a specific Anki notetype's field *names*, so the
importer/sync reader can read the right field by name instead of guessing by
position or HTML heuristics.

Only notetypes that have a profile here bypass ``sqlite_reader``'s heuristics.
The Slovene decks deliberately have **no** profile: that deck mixes several
notetypes (Slovene Vocabulary, Basic phonics, Pronunciation, Q&A) and the
existing positional/heuristic extraction is battle-tested against it — adding a
profile would risk a behavior change for no benefit. New languages whose deck
uses a single, well-named notetype (e.g. Norwegian's 17-field
"6000 Most Frequent Norwegian Words", where the L2 lives in "Norwegian word",
not field 0) declare a profile and skip the heuristics entirely.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.models.syntactic_unit import BackFieldTier


@dataclass(frozen=True)
class BackFieldSpec:
    """Declares one rich back-of-card field a notetype carries.

    ``field_name`` is the Anki field to read; ``label`` is shown to the learner;
    ``tier`` controls where it renders (summary / details / deep — see
    ``app.models.syntactic_unit.BackFieldTier``).
    """

    field_name: str
    label: str
    tier: BackFieldTier = "details"


@dataclass(frozen=True)
class NotetypeProfile:
    """Maps extraction roles to a notetype's field names.

    ``disambig`` is optional — a notetype with no disambiguation field leaves it
    ``None`` (extraction yields an empty disambig key). ``back_fields`` lists the
    secondary fields (IPA, inflections, dictionary entry…) surfaced on the card
    back; empty for notetypes that carry none.

    ``examples`` / ``inflections`` name the same fields ``back_fields`` renders,
    but declare a second *role* for them: raw material for a cloze production
    card when a word cannot be imaged. A notetype that leaves them ``None`` has
    no cloze source of its own and falls through to the (unbuilt) LLM tier.

    ``disambig_upos`` maps this deck's own part-of-speech vocabulary onto UPOS
    tags, so the closed-class test can go through the language registry
    (``is_function_word``) instead of matching deck labels anywhere in the sync
    path. The imported Norwegian deck writes English POS names into ``Word
    class``; another deck will write something else, which is exactly why this
    is per-notetype data rather than logic.
    """

    l2: str  # field name holding the L2 (target-language) word
    translation: str  # field name holding the English gloss
    disambig: str | None = None  # field name holding the disambig key, if any
    article: str | None = None  # field name holding the gender article (en/ei/et), if any
    back_fields: tuple[BackFieldSpec, ...] = field(default_factory=tuple)
    examples: str | None = None  # field name holding glossed example sentences
    inflections: str | None = None  # field name holding the inflection table
    disambig_upos: Mapping[str, str] = field(default_factory=dict)  # this deck's POS label → UPOS


_PROFILES: dict[str, NotetypeProfile] = {
    "6000 Most Frequent Norwegian Words": NotetypeProfile(
        l2="Norwegian word",
        translation="English translation",
        # Word class disambiguates homographs that share a surface form — e.g.
        # "løfte" (noun "promise" vs verb "lift"), "vår" ("our" vs "spring"),
        # "om" (3 senses). Without it they collapse to one GUID and one survives.
        # A true same-class duplicate (e.g. "foran" listed twice as preposition)
        # still shares a GUID and correctly merges.
        disambig="Word class",
        # Gender/indefinite article (en/ei/et) — shown as a display-time prefix on
        # noun headwords ("en orden"). Blank for non-nouns in the source deck.
        article="Article",
        # Rich back-of-card fields. Order here = render order. Tiers: "summary"
        # is always visible on the answer; "details" sits in a collapsed
        # disclosure; "deep" (the verbose dictionary entry) gets its own nested
        # disclosure so it stays out of the way until explicitly opened. The
        # audio/frequency-index fields are intentionally omitted — audio is
        # handled by the media pipeline, not the text back.
        back_fields=(
            BackFieldSpec("IPA", "IPA", "summary"),
            BackFieldSpec("General meaning", "Meaning", "summary"),
            BackFieldSpec("Context/nuance", "Nuance", "details"),
            BackFieldSpec("Inflections", "Inflections", "details"),
            BackFieldSpec("Gradbøying", "Comparison", "details"),
            BackFieldSpec("Example sentences", "Examples", "details"),
            BackFieldSpec("Note", "Note", "details"),
            BackFieldSpec("Dictionary entry", "Dictionary entry", "deep"),
        ),
        # Cloze material for words that image badly. `Example sentences` is
        # 98.7% populated and glossed; `Inflections` supplies the surface to
        # blank when the headword appears inflected in its own example (31.8% of
        # the words awaiting promotion). See app.cards.cloze_source.
        examples="Example sentences",
        inflections="Inflections",
        # The deck's own `Word class` vocabulary, mapped onto UPOS so the
        # closed-class routing runs through the language registry. Counts in the
        # deck: noun 1445, verb 615, adjective 537, adverb 183, preposition 72,
        # determinative 62, interjection 35, conjunction 22, pronoun 19.
        disambig_upos={
            "noun": "NOUN",
            "verb": "VERB",
            "adjective": "ADJ",
            "adverb": "ADV",
            "preposition": "ADP",
            "determinative": "DET",
            "interjection": "INTJ",
            "conjunction": "CCONJ",
            "pronoun": "PRON",
        },
    ),
}


def get_profile(notetype_name: str) -> NotetypeProfile | None:
    """Return the field-role profile for *notetype_name*, or ``None``.

    ``None`` means "no profile" — the caller falls back to the positional/HTML
    heuristics in ``sqlite_reader``.
    """
    return _PROFILES.get(notetype_name)


def inflection_labels() -> frozenset[str]:
    """Extras labels under which a profile's inflection table is stored.

    A profile names its inflection table twice: once as a ``back_fields`` spec
    (which decides the *label* the table is stored under in ``collocations.extras``)
    and once as the ``inflections`` role. Intersecting the two yields the labels a
    reader must look under, without a per-deck literal anywhere in core — a second
    deck that calls its table something else is picked up by declaring a profile.

    Deliberately excludes every other table-shaped field. Norwegian's
    ``Gradbøying`` (label ``Comparison``) is also an HTML table, but its
    ``<tbody>`` carries the grammar labels *komparativ* / *superlativ* as cells
    rather than in ``<thead>``, so reading it would feed those words into the
    inflected-form index as if they were surfaces.
    """
    return frozenset(
        spec.label
        for profile in _PROFILES.values()
        if profile.inflections
        for spec in profile.back_fields
        if spec.field_name == profile.inflections
    )
