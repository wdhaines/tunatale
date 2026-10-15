"""The affix drill: one affix pattern, drilled Pimsleur-style (tunatale-ve4p.4).

Built with no model call. For one pattern (Cebuano ``mag-`` / ``nag-``) and a
handful of roots the learner knows, the script is:

1. **Meet the pair.** "Listen and repeat", then each root, then each of its
   forms after its English, built up the Key Phrases way (whole, the pieces
   from the end, whole again: the owner's Pimsleur verdict, tunatale-ve4p.16).
   When the pattern is new to the learner the narrator says, after the first
   root, what the two forms are for (tunatale-ve4p.25).
2. **Your turn.** Each form asked by its English; the answer follows.
3. **New roots.** Roots named but NOT modelled: the learner has to build the
   form. Getting these right is the affix, not a remembered word.
4. **Everything, once more**: every form of every root again, the new roots'
   among the modelled ones.
5. **Whole lines**, when the caller supplies any: every line taught first,
   built up like a form, and only then asked for (tunatale-ve4p.20).

Every asking round is in mixed order (:func:`_mixed`, tunatale-ve4p.19): no two
neighbours are forms of one root, so no prompt is half given away by the one
before it, and neither the root nor the tense can be told from the position.
The order is drawn from what the drill is made of, so the same drill is the
same script every time it is built.

Two things can stop a root being drilled, and both DROP it with a reason
rather than guess: the language cannot vouch for every form
(``pattern_forms``), or nobody has written down what the forms mean
(``pattern_glosses``). The English is never worded from the root's own gloss:
an affix can change the meaning (Cebuano ``kita`` is "see", ``magkita`` is
"will meet"). A drill left with fewer than :data:`MIN_ROOTS` roots is an error.

This module only writes the script. Voices, pauses and the section type that
carries it into a lesson are the renderer's and the lesson model's business.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import Literal

from app.generation.section_builder import build_word_breakdown_spans
from app.generation.syllabify import syllabify_word
from app.languages import get_a1_morphology
from app.srs.a1_morphology import AffixPattern

MIN_ROOTS = 2

_NO_PATTERNS = "the language registers no affix patterns"
_NO_FORMS = "native news does not use every form"
_NO_ENGLISH = "no checked English for this root in this pattern"
_NO_SENTENCES = "no checked sentences for this root"
_LISTEN = "Listen and repeat."


@dataclass(frozen=True)
class DrillStep:
    """One spoken line. ``model`` is said for the learner, ``prompt`` asks, and
    ``answer`` follows the pause a prompt leaves.

    A piece of a built-up word carries the word and its syllable span, as a
    Key Phrases piece does, so it is voiced from the word's reading.

    ``stressed`` is the focus, as a span of ``text``: what the narrator says
    with the weight on it in a sentence drill, and ``None`` everywhere else.
    """

    kind: Literal["model", "prompt", "answer"]
    text: str
    language_code: str
    source_word: str | None = None
    span: tuple[int, int] | None = None
    stressed: tuple[int, int] | None = None


@dataclass
class AffixDrill:
    steps: list[DrillStep] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (root, why)


@dataclass(frozen=True)
class DrillRoot:
    root: str
    english: str
    cells: tuple[tuple[str, str], ...]  # (form, its English), in the pattern's order
    # The forms themselves, one per cell. A word pattern's cells ARE its forms,
    # so it leaves this empty and ``forms`` reads it off the cells.
    _forms: tuple[str, ...] = field(default=(), repr=False, compare=False)
    stressed: tuple[tuple[int, int], ...] = ()  # per cell, the focus as a span of its English
    contrast: int = 0  # how many leading cells are the pattern before this one's own
    regular: bool = True  # whether every form is one a rule could build

    @property
    def forms(self) -> tuple[str, ...]:
        """The forms themselves: a word pattern's cells ARE its forms."""
        return self._forms or tuple(form for form, _ in self.cells)

    @property
    def shown(self) -> tuple[tuple[str, str], ...]:
        """The cells this pattern teaches: a sentence pattern's first two are only for contrast."""
        return self.cells[self.contrast :]

    @property
    def transparent(self) -> bool:
        """Whether a form's English is built on the root's own.

        ``lakaw`` is "walk" and ``molakaw`` "will walk", so a learner who
        knows the root can be asked for a form nobody modelled. ``uban`` is
        "accompany" and ``mouban`` "will go along": that one has to be shown
        first, because nothing in the prompt leads to it.

        A root no rule spells (``imnon`` from ``inom``) is never transparent.
        """
        if not self.regular:
            return False
        root = self.english.split()
        return any(
            english.split()[i : i + len(root)] == root for _, english in self.cells for i in range(len(english.split()))
        )


def drill_root(language_code: str, root: str, pattern: AffixPattern) -> DrillRoot | str:
    """*root* ready to drill in *pattern*, or the reason it cannot be."""
    bundle = get_a1_morphology(language_code)
    if pattern.sentences:
        if bundle is None or bundle.pattern_sentences is None:
            return _NO_PATTERNS
        sentences = bundle.pattern_sentences(root, pattern)
        if sentences is None:
            return _NO_SENTENCES
        return DrillRoot(
            root,
            sentences.english,
            sentences.cells,
            sentences.forms,
            sentences.stressed,
            sentences.contrast,
            sentences.regular,
        )
    if bundle is None or bundle.pattern_forms is None or bundle.pattern_glosses is None:
        return _NO_PATTERNS
    forms = bundle.pattern_forms(root, pattern)
    if forms is None:
        return _NO_FORMS
    glosses = bundle.pattern_glosses(root, pattern)
    if glosses is None:
        return _NO_ENGLISH
    english, form_english = glosses
    return DrillRoot(root, english, tuple(zip(forms, form_english, strict=True)))


def _round(asks: list[tuple[str, str]], language_code: str) -> list[DrillStep]:
    """One round of "your turn": each of *asks* is ``(English prompt, answer)``.

    The narrator asks 'How do you say "X"?' on the round's first prompt and
    gives the English alone after that. Chosen by ear over a whole round
    (2026-10-12) against "Say: X." on every prompt, the question on every
    prompt, and the bare English throughout: "Say: X." read the English as the
    tail of a sentence and no punctuation of it changed that, and the question
    every time is more than a round needs once it has been asked. The question
    mark belongs to the frame, so the first prompt's own closing mark is dropped.
    """
    steps: list[DrillStep] = []
    for i, (english, answer) in enumerate(asks):
        prompt = english if i else f'How do you say "{english.rstrip(".?!")}"?'
        steps += [DrillStep("prompt", prompt, "en"), DrillStep("answer", answer, language_code)]
    return steps


def _named(found: DrillRoot, language_code: str) -> list[DrillStep]:
    return [DrillStep("model", found.english, "en"), DrillStep("model", found.root, language_code)]


_ASKED = 'How do you say "'


def _focused(step: DrillStep, focus: dict[str, tuple[int, int]]) -> DrillStep:
    """*step* carrying the focus of its text, when its text says a cell's English.

    The bare English model step and a bare prompt say the sentence whole, so
    the cell's own span stands on them; the round's first prompt wraps the
    English in ``How do you say "…"`` and the span moves past the frame.
    """
    if step.language_code != "en":
        return step
    shift = 0
    span = focus.get(step.text)
    if span is None and step.text.startswith(_ASKED) and step.text.endswith('"?'):
        shift = len(_ASKED)
        span = focus.get(f"{step.text[len(_ASKED) : -2]}.")
    if span is not None:
        return replace(step, stressed=(span[0] + shift, span[1] + shift))
    return step


@dataclass
class _Built:
    """What a drill has already built up, piece by piece (the owner, 2026-10-14).

    "We shouldn't need to Pimsleur rebuild the root once the root is
    introduced." A root's syllables are built once, by its first form; a later
    word on that root adds only its affix, and a word built before is said whole.
    """

    language_code: str
    words: set[str] = field(default_factory=set)
    roots: set[str] = field(default_factory=set)

    def _syllables(self, word: str) -> list[str]:
        return syllabify_word(word, self.language_code)

    def _root_start(self, word: str) -> int | None:
        """The syllable *word*'s built root starts at, or ``None`` when it has none.

        Only a root the word ENDS in and whose syllables are the word's last
        ones: a prefix pattern. An infix or a root the syllables cut across is
        left alone, which costs repetition and never a missing piece.
        """
        syls = self._syllables(word)
        for root in self.roots:
            root_syls = self._syllables(root)
            k = len(root_syls)
            if 0 < k < len(syls) and "".join(syls[-k:]).lstrip("-") == root:
                return len(syls) - k
        return None

    def keeps(self, source_word: str | None, span: tuple[int, int] | None) -> bool:
        """Whether a breakdown piece is still news. Bookends, multi-word
        partials and a word's whole-word rung always are."""
        if source_word is None or span is None:
            return True
        word = source_word.lower()
        if span == (0, len(self._syllables(word))):
            return True
        if word in self.words:
            return False
        start = self._root_start(word)
        return start is None or span[0] < start

    def add(self, steps: list[DrillStep], root: str | None = None) -> None:
        self.words.update(step.source_word.lower() for step in steps if step.source_word and step.span)
        if root is not None:
            self.roots.add(root)


def _taught(english: str, text: str, language_code: str, built: _Built) -> list[DrillStep]:
    """*text* after its English, built up as Key Phrases builds a phrase.

    The breakdown opens and closes on the whole of *text*, so the learner hears
    it, repeats it piece by piece, and hears it whole again; every target-language
    step leaves its own repeat gap (``pause_calculator``). Pieces of what
    *built* says the drill has already built are left out.
    """
    steps = [
        DrillStep("model", chunk.text, language_code, chunk.source_word, chunk.span)
        for chunk in build_word_breakdown_spans(text, language_code)
        if built.keeps(chunk.source_word, chunk.span)
    ]
    return [DrillStep("model", english, "en"), *steps]


def _mixed(
    found: list[DrillRoot], language_code: str, rng: random.Random, *, sentence: bool = False
) -> list[DrillStep]:
    """One round asking every form of every root in *found* once, in an order drawn from *rng*.

    One lap of the roots per cell. Each lap takes the roots in an order of its
    own and does not open on the root the last one closed on, so no two
    neighbours are forms of one root. Which cell a root gives up in which lap
    is drawn per root, so the tense does not follow from the position either.
    A single root has nobody to stand between its forms and is asked them in
    a row.

    *sentence* asks a cell's English as it stands: a sentence brings its own
    full stop, and "I'll drink water.." would be read oddly. A word cell's
    English gets one added.
    """
    cells = {root.root: rng.sample(root.cells, len(root.cells)) for root in found}
    asks: list[tuple[str, str]] = []
    last = None
    for _ in found[0].cells:
        lap = rng.sample(found, len(found))
        if lap[0] is last:
            lap.append(lap.pop(0))
        for root in lap:
            form, english = cells[root.root].pop()
            asks.append((english if sentence else f"{english}.", form))
        last = lap[-1]
    return _round(asks, language_code)


def build_affix_drill(
    language_code: str,
    pattern: AffixPattern,
    *,
    roots: list[str],
    new_roots: list[str] | None = None,
    lines: list[tuple[str, str]] | None = None,
    note: tuple[str, ...] = (),
) -> AffixDrill:
    """The drill script for *pattern*.

    *roots* are modelled and then asked; *new_roots* are only asked. Each of
    *lines* is ``(target-language line, its English)``. *note* is what the
    narrator says about the pattern's forms, once, straight after the first
    root has been modelled: its sentences speak of "the first" and "the
    second", which are then the two forms the learner has just heard.
    """
    drill = AffixDrill()

    def usable(candidates: list[str]) -> list[DrillRoot]:
        kept = []
        for root in candidates:
            found = drill_root(language_code, root, pattern)
            if isinstance(found, str):
                drill.dropped.append((root, found))
            else:
                kept.append(found)
        return kept

    modelled = usable(roots)
    if len(modelled) < MIN_ROOTS:
        why = "; ".join(f"{root}: {reason}" for root, reason in drill.dropped) or "none were given"
        raise ValueError(f"a {pattern.key} drill needs at least {MIN_ROOTS} roots, got {len(modelled)} usable ({why})")

    drill.steps.append(DrillStep("model", _LISTEN, "en"))
    built = _Built(language_code)
    for i, found in enumerate(modelled):
        drill.steps += _named(found, language_code)
        for cell, (form, english) in enumerate(found.cells):
            # The cells before ``contrast`` are a pattern the learner already
            # has: said whole, because there is nothing on it to build. The
            # rest are new and built up the Key Phrases way.
            taught = (
                [DrillStep("model", english, "en"), DrillStep("model", form, language_code)]
                if cell < found.contrast
                else _taught(english, form, language_code, built)
            )
            built.add(taught, root=found.root)
            drill.steps += taught
        if i == 0:
            drill.steps += [DrillStep("model", sentence, "en") for sentence in note]
    # Seeded by a string, which Python hashes itself: the interpreter's hash
    # salt does not reach it, so the order survives a restart.
    rng = random.Random(repr((pattern.key, roots, new_roots, lines)))
    drill.steps += _mixed(modelled, language_code, rng, sentence=pattern.sentences)
    unmodelled = usable(new_roots or [])
    if unmodelled:
        for found in unmodelled:
            drill.steps += _named(found, language_code)
        drill.steps += _mixed(unmodelled, language_code, rng, sentence=pattern.sentences)
    drill.steps += _mixed([*modelled, *unmodelled], language_code, rng, sentence=pattern.sentences)
    if lines:
        drill.steps.append(DrillStep("model", _LISTEN, "en"))
        for text, english in lines:
            taught = _taught(english, text, language_code, built)
            built.add(taught)
            drill.steps += taught
        drill.steps += _round([(english, text) for text, english in lines], language_code)
    if pattern.sentences:
        focus = {
            english: span
            for root in (*modelled, *unmodelled)
            for (_, english), span in zip(root.cells, root.stressed, strict=True)
        }
        drill.steps = [_focused(step, focus) for step in drill.steps]
    return drill
