"""Tagalog language plugin."""

from pathlib import Path

from app.cards.field_map import NotetypeProfile
from app.cards.vocab_notetype import TAGALOG_VOCAB
from app.languages import LanguageConfig, PlannerExample, register
from app.models.language import NARRATOR_VOICE, Language
from app.plugins.languages.tl.phoneme_plan import create_phoneme_planner
from app.plugins.languages.tl.preprocessor import TagalogPreprocessor
from app.plugins.languages.tl.syllabify import syllabify_tagalog_word
from app.plugins.languages.tl.verb_headword import verb_headword

_DATA = Path(__file__).parent / "data"
_style_notes = (_DATA / "style.md").read_text(encoding="utf-8").strip()

register(
    "tl",
    LanguageConfig(
        language=Language(
            code="tl",
            name="Tagalog",
            native_name="Tagalog",
            script="latin",
            tts_locale="fil-PH",
            tts_voice_map={
                "narrator": NARRATOR_VOICE,
                # Azure serves fil-PH with exactly two standard voices, Blessica
                # and Angelo, so the second woman and second man are Multilingual
                # voices under a <lang xml:lang="fil-PH"> wrapper. Chosen by
                # measurement on 2026-09-22 (tunatale-w4m7.3), 10 Tagalog A1
                # sentences, WER first and pitch distance second (the order the
                # user set for the nb cast; see tunatale-rag.6):
                #   fil-PH STT WER: 3 errors are shared by EVERY voice, natives
                #   included (isang kilo -> 1kg, istasyon -> estasyon): the
                #   transcriber, not the voice. Voice-specific errors: 0 for
                #   Emma, Samuel, Florian, Lola, Arabella; 1 for Lewis, Shimmer,
                #   Jorge; 3 for Alloy, Derek, Dustin.
                #   Median F0 (Praat), measured on the Tagalog clips themselves:
                #     Blessica 210.2 | Emma 170.7 (d39.5)
                #     Angelo   117.6 | Samuel 141.0 (d23.4) | Florian 129.3 (d11.7)
                # Florian had been the provisional male-2. At 11.7 Hz he is under
                # the 13.7 Hz minimum gap of the nb cast, the same near-duplicate
                # defect that retired William there. ⚠️ The nb pitch map does not
                # transfer across languages (Dustin 160.0 Hz on Norwegian, 136.5
                # on Tagalog), so measure on this language's text.
                "female-1": "fil-PH-BlessicaNeural",
                "female-2": "en-US-EmmaMultilingualNeural",
                "male-1": "fil-PH-AngeloNeural",
                "male-2": "en-US-SamuelMultilingualNeural",
                "female": "fil-PH-BlessicaNeural",
                "male": "fil-PH-AngeloNeural",
                # The key-phrase breakdown, outside the dialogue cast
                # (tunatale-w4m7.16). The breakdown plays syllable fragments as
                # IPA, and both fil-PH voices IGNORE IPA (byte-identical audio
                # for "salamat" under its own IPA and under the IPA of
                # "kumusta"). Multilingual voices honour it only unwrapped, read
                # by their own front end, so the base language matters: es/it
                # voices dropped ŋ, h or ʔ, and the German voices said all of
                # them (fil-PH STT on pangalan, kahapon, abuloy). The user chose
                # Seraphina by ear, 2026-09-23. Florian ranked first before the
                # creak on short open syllables was found (0-75 ms voiced at
                # ~100 Hz on "sa"/"ka"/"ma"; Seraphina 210-258 ms at 165-177 Hz,
                # every variant). Seraphina's renders vary call to call, so a
                # sound-merge check on her cannot compare single-render hashes.
                "key-phrases": "de-DE-SeraphinaMultilingualNeural",
            },
            # Per-voice loudness gains (dB) applied at assembly, target −20.0
            # LUFS: integrated loudness of the same 10 clips per voice
            # (ffmpeg ebur128), 2026-09-22. Control: Emma measures -18.1 here,
            # the same -1.9 dB gain the sl table measured on Slovene text.
            # Blessica's +1.8 keeps her true peak (-5.3 dBFS) well under the
            # renderer's -1.0 dBFS clamp.
            # en-US-GuyNeural is the shared narrator; the gain is resolved per
            # language (get_tts_voice_gain_db takes a code), so the same measured
            # value has to appear in every plugin table. Measured 2026-09-13 at
            # -19.08 LUFS mean.
            tts_voice_gain_db={
                "fil-PH-BlessicaNeural": 1.8,
                "fil-PH-AngeloNeural": -0.6,
                "en-US-EmmaMultilingualNeural": -1.9,
                "en-US-SamuelMultilingualNeural": -0.1,
                # The key-phrases voice, 2026-09-23 on 10 Tagalog sentences (the
                # wake lesson's key phrases plus three), same method: -21.84 LUFS.
                # The controls reproduced the table within 0.5 dB (Blessica -21.3
                # vs -21.8, Emma -18.3 vs -18.1).
                "de-DE-SeraphinaMultilingualNeural": 1.8,
                "en-US-GuyNeural": -0.9,
            },
        ),
        preprocessor_factory=TagalogPreprocessor,
        deck_name="2. Pimsleur Tagalog",
        # TT reads the whole Pimsleur tree (every card is in a Level::Lesson
        # subdeck) but mints its own cards into a subdeck of their own: the
        # user's call, 2026-09-23 (tunatale-w4m7.8). Create it in Anki first.
        mint_deck_name="2. Pimsleur Tagalog::TunaTale",
        # The Pimsleur deck's notetype (all 609 notes, measured 2026-09-23).
        # Scoped to Tagalog because the name is what EVERY genanki export is
        # called. Read from its template text: Card 1 (ord 0) is
        # {{Front}} -> {{Back}}{{Audio}}, English -> Tagalog, so it is the
        # PRODUCTION card and recognition is ord 1. Back ends in <br>, which
        # extract_l2's tag strip removes (tunatale-w4m7.8).
        notetype_profiles={
            "Basic (and reversed card) (genanki)": NotetypeProfile(
                l2="Back",
                translation="Front",
                # The English also keys the word: 5 Backs recur on two notes, 4
                # of them with different English (linggo "week" / Linggo
                # "Sunday", which the casefolded GUID would merge; kumain "eat" /
                # "have eaten"). Keyed on Back alone they shared a guid and every
                # sync re-imported one note over the other. Identical English
                # (binili, twice) still collides, and GUID_COLLISION says so.
                disambig="Front",
                recognition_ord=1,
            ),
        },
        vocab_notetype=TAGALOG_VOCAB,
        planner_example=PlannerExample(
            language_code="tl",
            day=5,
            title="At the market",
            focus="Buying fruit and asking prices",
            collocations=("magkano po ito?", "salamat po"),
            learning_objective="Ask prices and buy food politely in simple exchanges.",
            story_guidance="A morning at a Quezon City palengke; friendly haggling with a fruit vendor.",
        ),
        wordfreq_lang="fil",
        syllabifier_fn=syllabify_tagalog_word,
        lemma_table_path=_DATA / "tagalog_lemmas.tsv.gz",
        lemmatizer_type="table",
        verb_headword_fn=verb_headword,
        style_notes=_style_notes,
        function_words_path=_DATA / "function_words.json",
        numbers_path=_DATA / "numbers.json",
        # Key-phrase fragments as IPA from Wiktionary's narrow readings, for the
        # key-phrases voice (tunatale-w4m7.16; see tl/phoneme_plan.py).
        phoneme_planner_factory=create_phoneme_planner,
        # fil-PH ignores <phoneme>: the planned IPA must reach the voice unwrapped.
        ipa_read_in_voice_locale=True,
        # Deliberately omitted until their owning beads ship:
        # - l2_scorer: Tagalog has no letters that distinguish it from English,
        #   and None is a LOUD condition at the call site (see get_l2_scorer's
        #   docstring) — the deck-import bead uses a NotetypeProfile instead.
        # - morphology_profile, a1_morphology: out of scope for w4m7.6 (their
        #   owning beads ship them).
    ),
)
