"""The Cebuano card's dictionary layers, from note field to TT extras (tunatale-h0q3.2).

Extras are Anki-sourced: the pull sets ``collocations.extras`` from the note's
fields through the profile's ``back_fields``, so the layers reach TT only if
the profile declares them. The mint path fills the same fields from Wolff
through the registry (``dictionary_note_fields``), with no Cebuano logic in
core.
"""

from __future__ import annotations

from app.cards.field_map import get_profile
from app.cards.vocab_notetype import CEBUANO_VOCAB, DICTIONARY_FIELDS
from app.languages import dictionary_note_fields
from app.plugins.anki_sync.sqlite_reader import AnkiNote, extract_back_fields


def _note(**values: str) -> AnkiNote:
    by_name = {"Cebuano": "maayo", "English": "good", **values}
    names = list(CEBUANO_VOCAB.field_names)
    return AnkiNote(
        id=1,
        anki_guid="g",
        mid=1,
        mod=0,
        tags=[],
        fields=[by_name.get(n, "") for n in names],
        notetype_name=CEBUANO_VOCAB.name,
        field_names=names,
    )


def test_the_profile_tiers_the_five_fields():
    profile = get_profile(CEBUANO_VOCAB.name, "ceb")
    assert [(b.field_name, b.label, b.tier) for b in profile.back_fields] == [
        ("Spelling", "Spelling", "headword"),
        ("Meaning", "Meaning", "summary"),
        ("Verb", "Verb", "summary"),
        ("Details", "Details", "details"),
        ("Dictionary entry", "Dictionary entry", "deep"),
    ]
    assert {b.field_name for b in profile.back_fields} == set(DICTIONARY_FIELDS)


def test_a_filled_note_pulls_into_tiered_extras():
    extras = extract_back_fields(
        _note(Spelling="màáyo", Meaning="good", Details="<ol><li>good</li></ol>", **{"Dictionary entry": "<p>x</p>"}),
        "ceb",
    )
    assert [(e.label, e.html, e.tier) for e in extras] == [
        ("Spelling", "màáyo", "headword"),
        ("Meaning", "good", "summary"),
        ("Details", "<ol><li>good</li></ol>", "details"),
        ("Dictionary entry", "<p>x</p>", "deep"),
    ]


def test_a_note_without_the_layers_pulls_none():
    assert extract_back_fields(_note(), "ceb") == ()


def test_the_mint_facet_gives_wolffs_fields_by_note_field_name():
    fields = dictionary_note_fields("ceb", "maayo", "good")
    assert tuple(fields) == DICTIONARY_FIELDS
    assert fields["Spelling"] == "màáyo"
    assert fields["Dictionary entry"].startswith('<div class="wolff-cite">')


def test_a_word_wolff_does_not_settle_gets_no_fields():
    # A wrong spelling under a word is worse than none (wolff_cards docstring).
    assert dictionary_note_fields("ceb", "xyzzy", "nothing") == {}


def test_a_language_without_a_dictionary_gets_no_fields():
    assert dictionary_note_fields("no", "være", "to be") == {}
    assert dictionary_note_fields("zz-unregistered", "a", "b") == {}
