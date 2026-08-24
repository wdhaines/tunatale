"""X-SAMPA → IPA conversion for the NST pronunciation lexicon (Norwegian).

The mapping tables and the parser regex below are the work of **Per Erik
Solberg, National Library of Norway**, released into the **public domain
(CC0)**. They are transcribed unchanged from the upstream converter, which this
repo keeps at ``scripts/local/nst/convert_sampa.py`` as the provenance record.

They are **frozen reference data**: the NST lexicon itself is frozen at 2003, so
a table entry that looks wrong is a finding to report, not an edit to make —
changing one silently rewrites 649,591 transcriptions.

Two deliberate departures from upstream, both control flow rather than data:

- an unknown segment raises :class:`UnknownSegmentError` instead of calling
  ``sys.exit()``. Upstream is a script; this runs inside the API process, where
  exiting on bad input is not an option.
- :func:`strip_tone` is exposed separately rather than folded into the
  conversion. ``<phoneme>`` cannot carry Norwegian tone (tone marks are
  rejected with HTTP 400 and ``<prosody contour>`` is silently ignored), so the
  caller must strip — but the raw conversion stays lossless, because captions
  and lexicon-derived syllable boundaries both want the mark.

Stage 2a of lexicon adoption: this module is called by nothing.
"""

from __future__ import annotations

import re

# Tone 2 is marked with a literal double-quote in the converted IPA (see
# ``_SYLLABLE_CHARS``); tone 1 becomes "ˈ" and secondary stress "ˌ", both of
# which Azure accepts and which therefore survive :func:`strip_tone`.
_TONE_MARK = '"'

# Syllable boundary in converted IPA. ``_`` is a word boundary, which is
# necessarily also a syllable boundary — :func:`ipa_syllables` splits on both,
# matching ``lexicon.py``'s ``_syllables`` so "boundary" means one thing across
# the two modules.
_SYLLABLE_SEP = "."
_WORD_SEP = "_"

# --- upstream tables, transcribed unchanged (CC0, Per Erik Solberg) ---------

segdict = {
    "consonants": [
        ("b", "B", "b"),
        ("d", "D", "d"),
        ("f", "F", "f"),
        ("g", "G", "g"),
        ("h", "H", "h"),
        ("j", "J", "j"),
        ("k", "K", "k"),
        ("C", "KJ", "ç"),
        ("l", "L", "l"),
        ("m", "M", "m"),
        ("n", "N", "n"),
        ("N", "NG", "ŋ"),
        ("p", "P", "p"),
        ("r", "R", "r"),
        ("d`", "RD", "ɖ"),
        ("l`", "RL", "ɭ"),
        ("n`", "RN", "ɳ"),
        ("s`", "RS", "ʂ"),
        ("t`", "RT", "ʈ"),
        ("s", "S", "s"),
        ("S", "SJ", "ʃ"),
        ("t", "T", "t"),
        ("v", "V", "v"),
        ("w", "W", "w"),
    ],
    "vowels": [
        ("A:", "AA", "ɑː"),
        ("{:", "AE", "æː"),
        ("{", "AEH", "æ"),
        ("A", "AH", "ɑ"),
        ("@", "AX", "ə"),
        ("e:", "EE", "eː"),
        ("E", "EH", "ɛ"),
        ("I", "IH", "ɪ"),
        ("i:", "II", "ɪː"),
        ("l=", "LX", "l̩"),
        ("m=", "MX", "m̩"),
        ("n=", "NX", "n̩"),
        ("o:", "OA", "oː"),
        ("O", "OAH", "ɔ"),
        ("2:", "OE", "øː"),
        ("9", "OEH", "œ"),
        ("U", "OH", "ʊ"),
        ("u:", "OO", "uː"),
        ("l`=", "RLX", "ɭ̩"),
        ("n`=", "RNX", "ɳ̩"),
        ("r=", "RX", "r̩"),
        ("s=", "SX", "s̩"),
        ("u0", "UH", "ʉ"),
        ("}:", "UU", "ʉː"),
        ("Y", "YH", "ʏ"),
        ("y:", "YY", "yː"),
    ],
    "diphthongs": [
        ("{*I", "AEJ", "æ͡ɪ"),
        ("E*u0", "AEW", "æ͡ʉ"),
        ("A*I", "AJ", "ɑ͡ɪ"),
        ("9*Y", "OEJ", "œ͡ʏ"),
        ("O*Y", "OJ", "ɔ͡ʏ"),
        ("@U", "OU", "ɔ͡ʊ"),
    ],
}

_SAMPA_TO_IPA = {seg[0]: seg[2] for segtypelist in segdict.values() for seg in segtypelist}

syllcharmapping = {"$": ".", "_": "_", "¤": "¤", '"""': '"', '""': '"', '"': "ˈ", "%": "ˌ"}

_FULL_MAPPING: dict[str, str] = {**_SAMPA_TO_IPA, **syllcharmapping}

_TOTAL_PATTERN = re.compile(
    r"([bfghjkCNpSvw\$%¤_]|@(?!U)|[dt](?!`)|[sln](?![`=])|[mr](?!=)|[A{](?![:\*])|[O9E](?!\*)|(?<!\*)[IY]|(?<!@)U(?!:)|(?<!\")\"(?!\")|[dlnst]`(?!=)|[Aeio\{\}2uy]:|@U|\"{2}(?!\")|[lmnrs]=|(?<!\*)u0|_¤|[ln]`=|[\{A9O]\*[IY]|\"{3}|E\*u0)"
)

# --- end upstream tables ---------------------------------------------------


class UnknownSegmentError(ValueError):
    """An input contained something that is not a defined X-SAMPA segment."""


def _parse(sampa: str) -> list[str]:
    """Split *sampa* into segments, longest-match-first via the upstream regex."""
    spaced = _TOTAL_PATTERN.sub(r"\g<1> ", sampa)
    return spaced[:-1].split(" ")


def sampa_to_ipa(sampa: str) -> str:
    """Convert one NST-style X-SAMPA transcription to IPA.

    Raises :class:`UnknownSegmentError` if any segment is undefined.
    """
    ipa = []
    for segment in _parse(sampa):
        if segment not in _FULL_MAPPING:
            raise UnknownSegmentError(
                f"The input string {sampa!r} contains {segment!r}, which is not a defined X-SAMPA segment"
            )
        ipa.append(_FULL_MAPPING[segment])
    return "".join(ipa)


def strip_tone(ipa: str) -> str:
    """Remove tone-2 marks, leaving primary (ˈ) and secondary (ˌ) stress.

    Required before the IPA reaches ``<phoneme>``; never applied to IPA headed
    for a caption, which wants the mark.
    """
    return ipa.replace(_TONE_MARK, "")


def ipa_syllables(ipa: str) -> tuple[str, ...]:
    """Split converted *ipa* at syllable and word boundaries, dropping empties."""
    return tuple(s for s in ipa.replace(_WORD_SEP, _SYLLABLE_SEP).split(_SYLLABLE_SEP) if s)
