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
    """

    l2: str  # field name holding the L2 (target-language) word
    translation: str  # field name holding the English gloss
    disambig: str | None = None  # field name holding the disambig key, if any
    article: str | None = None  # field name holding the gender article (en/ei/et), if any
    back_fields: tuple[BackFieldSpec, ...] = field(default_factory=tuple)


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
    ),
}


def get_profile(notetype_name: str) -> NotetypeProfile | None:
    """Return the field-role profile for *notetype_name*, or ``None``.

    ``None`` means "no profile" — the caller falls back to the positional/HTML
    heuristics in ``sqlite_reader``.
    """
    return _PROFILES.get(notetype_name)
