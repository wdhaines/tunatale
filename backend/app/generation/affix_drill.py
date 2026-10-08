"""The affix drill: one affix pattern, drilled Pimsleur-style (tunatale-ve4p.4).

Built with no model call. For one pattern (Cebuano ``mag-`` / ``nag-``) and a
handful of roots the learner knows, the script is:

1. **Meet the pair.** Each root, then each of its forms after its English.
2. **Your turn.** Each form asked by its English, done-first so it is not an
   echo of the order just modelled; the answer follows.
3. **New on this root.** Roots named but NOT modelled: the learner has to
   build the form. Getting these right is the affix, not a remembered word.
4. **Once more, mixed**, when asked for: every form again, a different root
   each time. In rounds 2 and 3 a root's forms are asked back to back, so the
   second is half given away by the first; here it is not.
5. **Whole lines**, when the caller supplies any.

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

from dataclasses import dataclass, field
from typing import Literal

from app.languages import get_a1_morphology
from app.srs.a1_morphology import AffixPattern

MIN_ROOTS = 2

_NO_PATTERNS = "the language registers no affix patterns"
_NO_FORMS = "native news does not use every form"
_NO_ENGLISH = "no checked English for this root in this pattern"


@dataclass(frozen=True)
class DrillStep:
    """One spoken line. ``model`` is said for the learner, ``prompt`` asks, and
    ``answer`` follows the pause a prompt leaves."""

    kind: Literal["model", "prompt", "answer"]
    text: str
    language_code: str


@dataclass
class AffixDrill:
    steps: list[DrillStep] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (root, why)


@dataclass(frozen=True)
class DrillRoot:
    root: str
    english: str
    cells: tuple[tuple[str, str], ...]  # (form, its English), in the pattern's order

    @property
    def transparent(self) -> bool:
        """Whether a form's English is built on the root's own.

        ``lakaw`` is "walk" and ``molakaw`` "will walk", so a learner who
        knows the root can be asked for a form nobody modelled. ``uban`` is
        "accompany" and ``mouban`` "will go along": that one has to be shown
        first, because nothing in the prompt leads to it.
        """
        root = self.english.split()
        return any(
            english.split()[i : i + len(root)] == root for _, english in self.cells for i in range(len(english.split()))
        )


def drill_root(language_code: str, root: str, pattern: AffixPattern) -> DrillRoot | str:
    """*root* ready to drill in *pattern*, or the reason it cannot be."""
    bundle = get_a1_morphology(language_code)
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


def _prompts(found: DrillRoot, language_code: str) -> list[DrillStep]:
    steps: list[DrillStep] = []
    for form, english in reversed(found.cells):
        steps.append(DrillStep("prompt", f"Say: {english}.", "en"))
        steps.append(DrillStep("answer", form, language_code))
    return steps


def _named(found: DrillRoot, language_code: str) -> list[DrillStep]:
    return [DrillStep("model", found.english, "en"), DrillStep("model", found.root, language_code)]


def _mixed(found: list[DrillRoot], language_code: str) -> list[DrillStep]:
    """Every form of every root, once, with no two neighbours on the same root.

    One pass per cell; in each pass root *i* is asked for the cell *i* steps on
    from the pass's own, so the cell changes from one prompt to the next too.
    """
    steps: list[DrillStep] = []
    cells = len(found[0].cells)
    for turn in range(cells):
        for i, root in enumerate(found):
            form, english = root.cells[(i + turn) % cells]
            steps += [DrillStep("prompt", f"Say: {english}.", "en"), DrillStep("answer", form, language_code)]
    return steps


def build_affix_drill(
    language_code: str,
    pattern: AffixPattern,
    *,
    roots: list[str],
    new_roots: list[str] | None = None,
    lines: list[tuple[str, str]] | None = None,
    again: bool = False,
) -> AffixDrill:
    """The drill script for *pattern*.

    *roots* are modelled and then asked; *new_roots* are only asked. Each of
    *lines* is ``(target-language line, its English)``. *again* adds the mixed
    round, which asks every form a second time.
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

    for found in modelled:
        drill.steps += _named(found, language_code)
        for form, english in found.cells:
            drill.steps += [DrillStep("model", english, "en"), DrillStep("model", form, language_code)]
    for found in modelled:
        drill.steps += _prompts(found, language_code)
    unmodelled = usable(new_roots or [])
    for found in unmodelled:
        drill.steps += _named(found, language_code) + _prompts(found, language_code)
    if again:
        drill.steps += _mixed([*modelled, *unmodelled], language_code)
    for text, english in lines or []:
        drill.steps += [DrillStep("prompt", f"Say: {english}", "en"), DrillStep("answer", text, language_code)]
    return drill
