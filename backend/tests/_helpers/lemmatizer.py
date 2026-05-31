"""StubLemmatizer for testing — returns canned analyses without a real NLP pipeline."""

from __future__ import annotations

from app.srs.lemmatizer import Lemmatizer, TokenAnalysis


class StubLemmatizer:
    """Lemmatizer that returns canned results — no real NLP.

    Register mappings via ``set_lemma(word, lemma)``, ``set_analysis(word, lemma, case, number, person, upos, gender)``,
    or ``set_sentence(sentence, analyses)``. Unregistered words fall through to lowercase.
    """

    def __init__(self) -> None:
        self._lemmas: dict[str, str] = {}
        self._analyses: dict[str, TokenAnalysis] = {}
        self._sentence_analyses: dict[str, list[TokenAnalysis]] = {}

    def set_lemma(self, word: str, lemma: str) -> None:
        self._lemmas[word] = lemma

    def set_analysis(
        self,
        word: str,
        lemma: str,
        case: str = "",
        number: str = "",
        person: str = "",
        upos: str = "",
        gender: str = "",
    ) -> None:
        self._analyses[word] = TokenAnalysis(
            surface=word,
            lemma=lemma,
            upos=upos,
            case=case,
            number=number,
            person=person,
            gender=gender,
        )

    def set_sentence(self, sentence: str, analyses: list[TokenAnalysis]) -> None:
        self._sentence_analyses[sentence] = analyses

    def lemmatize(self, word: str, language_code: str) -> str:
        return self._lemmas.get(word, word.lower())

    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
        ta = self._analyses.get(word)
        if ta is not None:
            return ta.lemma, ta.case, ta.number
        return word.lower(), "", ""

    def analyze_sentence(self, sentence: str, language_code: str) -> list[TokenAnalysis]:
        if sentence in self._sentence_analyses:
            return self._sentence_analyses[sentence]
        tokens = sentence.split()
        return [
            self._analyses.get(
                t,
                TokenAnalysis(
                    surface=t,
                    lemma=self._lemmas.get(t, t.lower()),
                    upos="",
                    case="",
                    number="",
                    person="",
                    gender="",
                ),
            )
            for t in tokens
        ]


def assert_satisfies_lemmatizer_protocol(obj: object) -> None:
    """Assert that *obj* satisfies the ``Lemmatizer`` Protocol at runtime."""
    assert isinstance(obj, Lemmatizer), f"{type(obj).__name__} does not satisfy the Lemmatizer Protocol"
