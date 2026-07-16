"""Norwegian (Bokmål) syllabifier — onset-maximization with Norwegian phonotactics."""

from __future__ import annotations

from app.generation.syllabify import syllabify as _syllabify

# Norwegian (Bokmål) vowels include y and the special letters æ/ø/å.
_NO_VOWELS = frozenset("aeiouyæøå")

# Norwegian (Bokmål) diphthongs: a vowel + glide pair that forms a single
# syllable nucleus, so onset-maximization must not split them (``bøy|de`` not
# ``bø|y|de``, ``lei|lig`` not ``le|i|lig``). Deliberately conservative — only
# the native pairs that are almost never genuine hiatus.
_NO_DIPHTHONGS = frozenset(["ei", "øy", "au"])

# Valid consonant clusters that can begin a Norwegian syllable (onset
# maximization). Germanic phonotactics: stop/fricative + liquid/glide,
# s-clusters, and the palatal digraphs (kj/gj/sj/skj/tj/fj).
_NO_VALID_ONSETS = frozenset(
    [
        # Three-consonant onsets
        "str",
        "spr",
        "skr",
        "skv",
        "spl",
        "skj",
        "stj",
        # Stop/fricative + liquid
        "bl",
        "br",
        "dr",
        "fl",
        "fr",
        "gl",
        "gr",
        "kl",
        "kr",
        "pl",
        "pr",
        "tr",
        "vr",
        # s-clusters
        "sk",
        "sl",
        "sm",
        "sn",
        "sp",
        "st",
        "sv",
        # Stop/fricative + glide or nasal, palatal digraphs
        "kn",
        "kv",
        "gn",
        "kj",
        "gj",
        "sj",
        "tj",
        "fj",
        "hj",
        "hv",
        "pj",
        "bj",
        "dv",
        "tv",
    ]
)

# Onsets that are valid *word-initially* (kne, kniv, gni) but never occur as a
# medial onset in Norwegian — a stop+nasal cluster between two vowels closes the
# preceding syllable rather than opening the next (tek·nisk, reg·ne — not
# te·knisk, re·gne).
_NO_INITIAL_ONLY_ONSETS = frozenset(["kn", "gn", "pn"])


def syllabify_norwegian_word(word: str) -> list[str]:
    """Split a Norwegian (Bokmål) word into syllables."""
    return _syllabify(word, _NO_VOWELS, _NO_VALID_ONSETS, _NO_DIPHTHONGS, _NO_INITIAL_ONLY_ONSETS)
