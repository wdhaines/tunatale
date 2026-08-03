"""Keep a generated card's gloss from contradicting its headword.

TT builds a vocab card's front from the lemmatizer's lemma and its back from the
LLM's gloss, and nothing checked the two agreed. The LLM glosses a bare noun as
if it were definite, so cards shipped as ``morder`` → "the murderer" (definite
would be ``morderen``) and ``set`` → "the seat".

The definiteness test is language morphology and lives in the language plugin;
this module only reaches it through the registry, per the
no-hardcoded-language-logic rule. Languages with no checker registered are a
no-op, so this is inert for Slovene (which marks definiteness differently) and
for ``en``.

Direction matters: we only ever *remove* an article the headword doesn't support.
Adding one would require knowing the noun's gender, which the gloss cannot tell
us. A language whose checker is conservative therefore degrades to today's
behavior rather than to a wrong card.
"""

from __future__ import annotations

from app.languages import get_definite_form_checker

_ARTICLE = "the "


def align_gloss_definiteness(headword: str, gloss: str, language_code: str) -> str:
    """Drop a leading English definite article when *headword* is indefinite.

    Applied per slash-separated alternative, because glosses arrive as
    ``"the murderer / the killer"`` as often as as a single phrase. An article
    anywhere other than the start of an alternative is left alone — "through the
    wall" is a correct gloss for an indefinite headword.
    """
    if not gloss:
        return gloss
    checker = get_definite_form_checker(language_code)
    if checker is None or checker(headword):
        return gloss

    parts = gloss.split("/")
    aligned = []
    for part in parts:
        stripped = part.lstrip()
        if stripped.lower().startswith(_ARTICLE):
            lead = part[: len(part) - len(stripped)]
            aligned.append(lead + stripped[len(_ARTICLE) :])
        else:
            aligned.append(part)
    return "/".join(aligned)
