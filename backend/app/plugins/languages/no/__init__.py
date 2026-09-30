"""Norwegian language plugin."""

from pathlib import Path

from app.cards.vocab_notetype import NORWEGIAN_VOCAB
from app.languages import AlignmentConfig, BuiltData, LanguageConfig, PlannerExample, register
from app.models.language import NARRATOR_VOICE, Language
from app.plugins.languages.no.a1_morphology import NORWEGIAN_A1_MORPHOLOGY
from app.plugins.languages.no.alignment import MODEL_ID, NORWEGIAN_VOWELS, create_aligner
from app.plugins.languages.no.l2_scoring import score_norwegian_l2
from app.plugins.languages.no.lexicon import DB_PATH as NST_DB_PATH
from app.plugins.languages.no.lexicon import EXTRACT_PATH as NST_EXTRACT_PATH
from app.plugins.languages.no.lexicon import build_lexicon_db, create_nst_lexicon
from app.plugins.languages.no.morphology import is_definite_form, is_lemma_plausible
from app.plugins.languages.no.multiword import trapped_pairs
from app.plugins.languages.no.norwegian_breakdown import (
    build_norwegian_breakdown_spans,
    flat_syllables,
    slow_norwegian_word,
)
from app.plugins.languages.no.noun_gender import noun_gender
from app.plugins.languages.no.phoneme_plan import create_phoneme_planner
from app.plugins.languages.no.preprocessor import NorwegianPreprocessor
from app.plugins.languages.no.syllabify import syllabify_norwegian_word

_style_notes = (Path(__file__).parent / "data" / "style.md").read_text(encoding="utf-8").strip()

register(
    "no",
    LanguageConfig(
        language=Language(
            code="no",
            name="Norwegian",
            native_name="norsk",
            script="latin",
            tts_locale="nb-NO",
            tts_voice_map={
                "narrator": NARRATOR_VOICE,
                "female-1": "nb-NO-PernilleNeural",
                "female-2": "nb-NO-IselinNeural",
                "female-3": "en-US-EmmaMultilingualNeural",
                "female-4": "en-US-ShimmerTurboMultilingualNeural",
                "male-1": "nb-NO-FinnNeural",
                "male-2": "en-US-DerekMultilingualNeural",
                "male-3": "it-IT-GiuseppeMultilingualNeural",
                "male-4": "en-US-DustinMultilingualNeural",
                "female": "nb-NO-PernilleNeural",
                "male": "nb-NO-FinnNeural",
            },
            # Who reads each role's English in the translated sections
            # (tunatale-ucpg, confirmed by the user 2026-09-29). The five
            # Multilingual cast members keep their own voice, so a character
            # sounds like one person in both languages. Pernille, Iselin and
            # Finn speak only Norwegian and get pitch-matched English voices.
            # English median F0 on one dialogue line (the nb cast figures above
            # do not carry over — Dustin and Giuseppe are 32 Hz apart in
            # Norwegian and ~6 Hz apart in English; kept, on the user's ear):
            #   female-1 Nancy 175.8   female-2 Amanda 234.0   female-3 Emma 204.1
            #   female-4 Shimmer 149.6 male-1 Adam 105.9       male-2 Derek 120.5
            #   male-3 Giuseppe 146.6  male-4 Dustin 141.0     narrator Davis 113.0
            tts_en_voice_map={
                "female-1": "en-US-NancyMultilingualNeural",
                "female-2": "en-US-AmandaMultilingualNeural",
                "female-3": "en-US-EmmaMultilingualNeural",
                "female-4": "en-US-ShimmerTurboMultilingualNeural",
                "male-1": "en-US-AdamMultilingualNeural",
                "male-2": "en-US-DerekMultilingualNeural",
                "male-3": "it-IT-GiuseppeMultilingualNeural",
                "male-4": "en-US-DustinMultilingualNeural",
                "female": "en-US-NancyMultilingualNeural",
                "male": "en-US-AdamMultilingualNeural",
            },
            # The dialogue cast, measured 2026-09-16 through the product's own
            # synthesize() (ffmpeg ebur128 for loudness, Azure STT for WER):
            #   female-1 nb-NO-Pernille  163.3 Hz F0 / 0.722 harmonicity / WER 0.031 / 1 voice-specific error
            #   female-2 nb-NO-Iselin    205.1 Hz F0 / 0.821                / WER 0.042 / 1 voice-specific error
            #   female-3 en-US-Emma      181.8 Hz F0 / 0.773                / WER 0.021
            #   female-4 en-US-Shimmer   149.5 Hz F0 / 0.702                / WER 0.010
            #   male-1   nb-NO-Finn       97.0 Hz F0 / 0.538                / WER 0.021
            #   male-2   en-US-Derek     114.3 Hz F0 / 0.597                / WER 0.010
            #   male-3   it-IT-Giuseppe  128.0 Hz F0 / 0.650                / WER 0.010
            #   male-4   en-US-Dustin    160.0 Hz F0 / 0.734                / WER 0.010
            # Minimum pitch gap 13.8 Hz (women) / 13.7 Hz (men); the user
            # confirmed all eight by ear on a loudness-normalised track.
            # ⚠️ male-2 was en-AU-William until this cast, deliberately: Finn
            # 97.0 vs William 101.9 Hz is 4.9 Hz and 0.005 harmonicity apart —
            # a near-duplicate, the defect this widening exists to remove. Only
            # 2 of the other 31 nb-capable male voices sit closer to Finn than
            # William did.
            # ⚠️ en-US-ShimmerTurboMultilingualNeural is a Turbo voice whose
            # VoiceType is still Neural (read from the live /voices/list, which
            # has only Neural and NeuralHD) — NOT the paid HD line. Keep it.
            # Per-voice loudness gains (dB) applied at assembly, target −20.0
            # LUFS. Measured 2026-09-16 on ONE real lesson sentence set through
            # synthesize() (ffmpeg ebur128), so the cast is mutually
            # consistent — mixing rag.5's set with this one is what the rag.5
            # brief warns against. Constant per voice so intra-voice dynamics
            # are preserved. Keyed by VOICE, not role: a stored lesson pins a
            # RESOLVED voice_id per phrase, so William (male-2 until rag.6)
            # stays at 0.9 even though he left the map — an unknown voice
            # returns 0.0, which would silently un-normalise legacy audio on
            # the next re-render.
            # These are levels reading NORWEGIAN. English lines (the narrator,
            # and tts_en_voice_map below) resolve against Language.english()'s
            # table, measured on English text: a voice's two levels differ
            # (Giuseppe -0.7 here, +0.9 in English).
            tts_voice_gain_db={
                "nb-NO-PernilleNeural": 1.2,
                "nb-NO-IselinNeural": -0.1,
                "en-US-EmmaMultilingualNeural": -1.8,
                "en-US-ShimmerTurboMultilingualNeural": 0.4,
                "nb-NO-FinnNeural": 0.8,
                "en-US-DerekMultilingualNeural": 0.2,
                "it-IT-GiuseppeMultilingualNeural": -0.7,
                "en-US-DustinMultilingualNeural": 0.9,
                "en-AU-WilliamMultilingualNeural": 0.9,
            },
        ),
        preprocessor_factory=NorwegianPreprocessor,
        deck_name="0. 6000 Most Frequent Norwegian Words [Part 1]",
        vocab_notetype=NORWEGIAN_VOCAB,
        l2_scorer=score_norwegian_l2,
        lemmatizer_type="stanza",
        definite_form_fn=is_definite_form,
        lemma_plausible_fn=is_lemma_plausible,
        multiword_traps_fn=trapped_pairs,
        slow_word_fn=slow_norwegian_word,
        variant_separator=",",
        infinitive_marker="å",
        gender_articles={"Masc": "en", "Fem": "ei/en", "Neut": "et"},
        noun_gender_fn=noun_gender,
        syllabifier_fn=syllabify_norwegian_word,
        planner_example=PlannerExample(
            language_code="no",
            day=5,
            title="At the bakery",
            focus="Ordering pastries and paying",
            collocations=("et br\u00f8d, takk", "hvor mye koster det?"),
            learning_objective="Order food and handle payment in simple exchanges.",
            story_guidance="A quick visit to a Bergen bakery; friendly small talk with the baker.",
        ),
        style_notes=_style_notes,
        function_words_path=Path(__file__).parent / "data" / "function_words.json",
        numbers_path=Path(__file__).parent / "data" / "numbers.json",
        spatial_path=Path(__file__).parent / "data" / "spatial.json",
        pronouns_path=Path(__file__).parent / "data" / "pronouns.json",
        calendar_path=Path(__file__).parent / "data" / "calendar.json",
        wordfreq_lang="nb",
        breakdown_spans_fn=build_norwegian_breakdown_spans,
        alignment=AlignmentConfig(
            model_id=MODEL_ID,
            vowels=NORWEGIAN_VOWELS,
            aligner_factory=create_aligner,
            syllabify_fn=flat_syllables,
        ),
        a1_morphology=NORWEGIAN_A1_MORPHOLOGY,
        lexicon_factory=create_nst_lexicon,
        phoneme_planner_factory=create_phoneme_planner,
        # Built from Stanza by scripts/build_stanza_lemma_table.py (tunatale-kbb.18).
        lemma_table_path=Path(__file__).parent / "data" / "stanza_lemmas.tsv.gz",
        # The NST lexicon is gitignored and BUILT from its committed extract.
        # Registered so `python -m app.build_data` (Dockerfile, switch.sh) builds
        # it: before this nothing that ships did, and without it compound
        # splitting lost its veto silently ('forsvare' -> 'for, svare' on the
        # live instance, tunatale-ip8q).
        built_data=(BuiltData(extract=NST_EXTRACT_PATH, db=NST_DB_PATH, build=build_lexicon_db),),
    ),
)
