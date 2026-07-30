"""Slovene language plugin."""

from pathlib import Path

from app.cards.vocab_notetype import SLOVENE_VOCAB
from app.languages import LanguageConfig, PlannerExample, register
from app.models.language import NARRATOR_VOICE, Language
from app.plugins.languages.sl.preprocessor import SlovenePreprocessor
from app.plugins.languages.sl.syllabify import syllabify_slovene_word

_style_notes = (Path(__file__).parent / "data" / "style.md").read_text(encoding="utf-8").strip()

register(
    "sl",
    LanguageConfig(
        language=Language(
            code="sl",
            name="Slovene",
            native_name="slovenščina",
            script="latin",
            tts_voice_map={
                "narrator": NARRATOR_VOICE,
                "female-1": "sl-SI-PetraNeural",
                "female-2": "sl-SI-PetraNeural",
                "male-1": "sl-SI-RokNeural",
                "male-2": "sl-SI-RokNeural",
                "female": "sl-SI-PetraNeural",
                "male": "sl-SI-RokNeural",
            },
        ),
        preprocessor_factory=SlovenePreprocessor,
        deck_name="1. Slovene",
        vocab_notetype=SLOVENE_VOCAB,
        lemmatizer_type="classla",
        morphology_profile="slavic",
        syllabifier_fn=syllabify_slovene_word,
        planner_example=PlannerExample(
            language_code="sl",
            day=5,
            title="At the bakery",
            focus="Ordering pastries and paying",
            collocations=("en kruh, prosim", "koliko stane?"),
            learning_objective="Order food and handle payment in simple exchanges.",
            story_guidance="A quick visit to a Ljubljana bakery; friendly small talk with the baker.",
        ),
        style_notes=_style_notes,
        function_words_path=Path(__file__).parent / "data" / "function_words.json",
    ),
)
