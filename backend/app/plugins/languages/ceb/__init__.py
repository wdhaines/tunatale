"""Cebuano language plugin."""

from pathlib import Path

from app.cards.vocab_notetype import CEBUANO_VOCAB
from app.languages import LanguageConfig, register
from app.models.language import NARRATOR_VOICE, Language
from app.plugins.languages.ceb.phoneme_plan import create_phoneme_planner
from app.plugins.languages.ceb.preprocessor import CebuanoPreprocessor

_DATA = Path(__file__).parent / "data"
_style_notes = (_DATA / "style.md").read_text(encoding="utf-8").strip()

register(
    "ceb",
    LanguageConfig(
        language=Language(
            code="ceb",
            name="Cebuano",
            native_name="Binisaya",
            script="latin",
            tts_locale="ceb-PH",
            tts_voice_map={
                "narrator": NARRATOR_VOICE,
                # Gemini-TTS voices (the user's decision, 2026-09-25,
                # tunatale-u8nz.1), rendered through Cloud TTS with
                # languageCode=ceb-PH by app/audio/gemini_tts.py. The id shape
                # <locale>-<Name>Gemini mirrors Azure's <locale>-<Name>Neural on
                # purpose, so the registry-wide voice invariants (the locale
                # sliced off the id) hold unchanged, and the router dispatches
                # on the suffix.
                # Cast picked by PITCH DISTANCE, the tl/nb method (tunatale-u8nz.2,
                # 2026-09-25): median F0 (Praat) of the 8-sentence Cebuano sample
                # set, gemini-2.5-flash-tts, rate 1.0:
                #   F: Leda 215.1 | Kore 209.4 | Aoede 206.5 | Despina 185.5
                #   M: Orus 139.2 | Iapetus 128.5 | Puck 109.2 | Charon 107.2
                # Kore and Charon stay (the user heard them in the voice samples).
                # The provisional second voices were near-duplicates of them,
                # Aoede at 2.9 Hz from Kore and Puck at 2.0 Hz from Charon, far
                # under the nb cast's 13.7 Hz minimum gap. Despina (23.9 Hz from
                # Kore) and Orus (32.0 Hz from Charon) are the widest pairs.
                # ⚠️ Renders vary per call, so these are one render's numbers;
                # gaps of 24-32 Hz are robust to that, a 3 Hz one was not.
                "female-1": "ceb-PH-KoreGemini",
                "female-2": "ceb-PH-DespinaGemini",
                "male-1": "ceb-PH-CharonGemini",
                "male-2": "ceb-PH-OrusGemini",
                "female": "ceb-PH-KoreGemini",
                "male": "ceb-PH-CharonGemini",
            },
            # Per-voice loudness gains (dB) applied at assembly, target −20.0
            # LUFS: integrated loudness of the same 8 Cebuano sentences per
            # voice (ffmpeg ebur128), 2026-09-25. Gemini renders run loud
            # (-14.0 to -18.7 LUFS), so every gain is a cut.
            # en-US-GuyNeural is the shared narrator; the gain is resolved per
            # language (get_tts_voice_gain_db takes a code), so the same
            # measured value has to appear in every plugin table. Measured
            # 2026-09-13 at -19.08 LUFS mean.
            tts_voice_gain_db={
                "ceb-PH-KoreGemini": -6.0,
                "ceb-PH-DespinaGemini": -5.6,
                "ceb-PH-CharonGemini": -3.1,
                "ceb-PH-OrusGemini": -5.0,
                "en-US-GuyNeural": -0.9,
            },
        ),
        preprocessor_factory=CebuanoPreprocessor,
        deck_name="3. Bisaya",
        mint_deck_name="3. Bisaya::TunaTale",
        vocab_notetype=CEBUANO_VOCAB,
        # The style guide's first job is keeping Tagalog OUT: close kin, and
        # dominant in training data (tunatale-u8nz.4). All three data files are
        # orchestrator-drafted and not yet native-checked (tunatale-u8nz.9).
        style_notes=_style_notes,
        function_words_path=_DATA / "function_words.json",
        numbers_path=_DATA / "numbers.json",
        # The Gemini drill voices are told what to say by an instruction rather
        # than by markup, and that instruction needs the fragment's IPA — which
        # is what this factory supplies (tunatale-u8nz.1). It is the FIRST
        # planner not built on a pronunciation lexicon: the reading comes off
        # the spelling, in core, with only the letter table here.
        phoneme_planner_factory=create_phoneme_planner,
        # Multi-word drill steps get a per-word IPA map too, and only here
        # (brief-ceb-phrase-ipa, 2026-09-25): plain Gemini said "ilubong ugma"
        # as "ilubong uglak", and in the user's blind A/B the reading fixed it
        # (2 of 2 right, 2 of 3 wrong without). Deliberately NOT a default — the
        # channel is the adapter's, and Tagalog's drill runs on Azure, where a
        # per-word map would wrap every word in <phoneme> and change audio
        # nobody has listened to.
        ipa_for_drill_phrases=True,
        # Deliberately omitted until their owning beads ship:
        # - planner_example: get_planner_example picks the LOWEST-SORTING other
        #   language that supplies one, and "ceb" sorts before every other
        #   registered code. A Cebuano example would therefore silently replace
        #   the example shown to EVERY other language — and Tagalog, a close
        #   relative, would be shown Cebuano. That is exactly the
        #   planner-language contamination the selector exists to prevent.
        # - wordfreq_lang: wordfreq has no "ceb" (tunatale-u8nz.6).
        # - syllabifier_fn, lemma_table_path, lemmatizer_type (stays at the
        #   shared "lowercase" default): tunatale-u8nz.6 and tunatale-u8nz.5.
        #   The planner splitting words with syllabify_word is not a fourth
        #   omission to fix: that IS the splitter the breakdown uses, and
        #   registering a second one here would desynchronise the two.
        # - l2_scorer, notetype_profiles: later Cebuano beads.
    ),
)
