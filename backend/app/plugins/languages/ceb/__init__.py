"""Cebuano language plugin."""

from pathlib import Path

from app.cards.field_map import NotetypeProfile
from app.cards.vocab_notetype import CEBUANO_VOCAB
from app.languages import LanguageConfig, register
from app.models.language import NARRATOR_VOICE, Language
from app.plugins.languages.ceb.a1_morphology import CEBUANO_A1_MORPHOLOGY
from app.plugins.languages.ceb.phoneme_plan import create_phoneme_planner
from app.plugins.languages.ceb.preprocessor import CebuanoPreprocessor
from app.plugins.languages.ceb.syllabify import syllabify_cebuano_word

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
                # Narration lines keep the Cebuano voice that read them before
                # the role existed (Orus, lent by male-2); only their English
                # moves to the narrator (user, 2026-09-30, tunatale-fx5n).
                "narration": "ceb-PH-OrusGemini",
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
            # Who reads each role's English (tunatale-ucpg, the user's option 2,
            # 2026-09-29): pitch-matched Azure stand-ins for all four Gemini
            # voices, not the Gemini voices themselves, so the English bills
            # against the free Azure allowance and renders deterministically.
            # English median F0 on one dialogue line: Emma 204.1, Nancy 175.8,
            # Adam 105.9, Dustin 141.0 (vs Kore 209.4, Despina 185.5,
            # Charon 107.2, Orus 139.2 above).
            # male-1 is the exception to the pitch match (tunatale-fx5n,
            # 2026-10-01). Once narration's English moved to the narrator, the
            # user found Adam too close to him on this lesson's own lines, and
            # chose by ear from nine voices reading them. Pitch did not predict
            # it: Adam sat 10 Hz below the narrator's line and Lewis 27 above
            # (142.8 vs 115.6), but what the user heard was Adam's ACCENT
            # changing with the text, the same voice sounding southern on a
            # Norwegian lesson's English and not on this one's. Audition a
            # stand-in on the lesson's own lines, beside the voice it has to
            # differ from.
            # ⚠️ Lewis was the SECOND pick. Brandon, the first, clips short
            # one-word lines ("Why?", "Yes.", "No.": 110-150 ms of sound in
            # every request shape tried). An audition on two full sentences
            # cannot show that, so check a candidate's one-word lines too.
            tts_en_voice_map={
                "narration": NARRATOR_VOICE,
                "female-1": "en-US-EmmaMultilingualNeural",
                "female-2": "en-US-NancyMultilingualNeural",
                "male-1": "en-US-LewisMultilingualNeural",
                "male-2": "en-US-DustinMultilingualNeural",
                "female": "en-US-EmmaMultilingualNeural",
                "male": "en-US-LewisMultilingualNeural",
            },
            # Per-voice loudness gains (dB) applied at assembly, target −20.0
            # LUFS: integrated loudness of the same 8 Cebuano sentences per
            # voice (ffmpeg ebur128), 2026-09-25. Gemini renders run loud
            # (-14.0 to -18.7 LUFS), so every gain is a cut.
            # English lines resolve their gain in Language.english()'s table
            # (the renderer keys on the text's language), so the narrator has
            # no entry here.
            tts_voice_gain_db={
                "ceb-PH-KoreGemini": -6.0,
                "ceb-PH-DespinaGemini": -5.6,
                "ceb-PH-CharonGemini": -3.1,
                "ceb-PH-OrusGemini": -5.0,
            },
        ),
        preprocessor_factory=CebuanoPreprocessor,
        deck_name="3. Bisaya",
        mint_deck_name="3. Bisaya::TunaTale",
        vocab_notetype=CEBUANO_VOCAB,
        # TT's own vocab notes store the headword as plain text, with no L2
        # markup class, so without this the reader has to GUESS which field is
        # the Cebuano. Cebuano has no letters that tell it apart from English,
        # so there is no scorer to guess with, and the first Cebuano mint (74
        # starter cards, u8nz.7) failed its sync on exactly that. TT wrote the
        # notetype, so name its fields instead.
        notetype_profiles={
            CEBUANO_VOCAB.name: NotetypeProfile(
                l2=CEBUANO_VOCAB.l2_field, translation="English", disambig="DisambigKey"
            ),
        },
        # The style guide's first job is keeping Tagalog OUT: close kin, and
        # dominant in training data (tunatale-u8nz.4). All three data files are
        # orchestrator-drafted and not yet native-checked (tunatale-u8nz.9).
        style_notes=_style_notes,
        function_words_path=_DATA / "function_words.json",
        numbers_path=_DATA / "numbers.json",
        spatial_path=_DATA / "spatial.json",
        pronouns_path=_DATA / "pronouns.json",
        calendar_path=_DATA / "calendar.json",
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
        # Onset maximization with Cebuano phonotactics (tunatale-u8nz.6): `ng`
        # opens a syllable (pa|nga|lan) and loan clusters do too (es|kwe|la|han),
        # where the generic default cut pan|ga|lan and esk|we|la|han. The
        # phoneme planner and the breakdown both split through syllabify_word,
        # so registering it here moves both together.
        syllabifier_fn=syllabify_cebuano_word,
        # Roots first (tunatale-u8nz.5): affixed forms lemmatize to their root,
        # built from Wiktionary by scripts/build_cebuano_lemma_table.py.
        lemma_table_path=_DATA / "cebuano_lemmas.tsv.gz",
        lemmatizer_type="table",
        # wordfreq has no "ceb". Root-keyed counts from FineWeb-2's native
        # Cebuano news (tunatale-u8nz.6), built by
        # scripts/build_cebuano_frequency.py; see data/ATTRIBUTION.md.
        frequency_table_path=_DATA / "cebuano_frequency.tsv.gz",
        # Stage 2 (tunatale-u8nz.20): an affixed form (milakaw) becomes an
        # inflection cloze on its root card once the root is in production review.
        a1_morphology=CEBUANO_A1_MORPHOLOGY,
        # Deliberately omitted until their owning beads ship:
        # - planner_example: get_planner_example picks the LOWEST-SORTING other
        #   language that supplies one, and "ceb" sorts before every other
        #   registered code. A Cebuano example would therefore silently replace
        #   the example shown to EVERY other language — and Tagalog, a close
        #   relative, would be shown Cebuano. That is exactly the
        #   planner-language contamination the selector exists to prevent.
        # - l2_scorer: nothing to score with (see notetype_profiles above); a
        #   deck the user imports will need its own profile, as Tagalog's did.
    ),
)
