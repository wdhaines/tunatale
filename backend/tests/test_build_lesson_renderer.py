"""One way to build a LessonRenderer, so a re-render sounds like a fresh render.

Live 2026-09-24: ``scripts/regen_key_phrases.py`` built its own renderer and
omitted ``tts_locales``. An omitted entry means "declare nothing", so every
plain-text Tagalog key phrase ("sa lamay", "ng abuloy") reached the German
key-phrases voice with no ``<lang xml:lang="fil-PH">`` and was read as German:
"sa" and "ng" were spelled letter by letter. The app's own renderer
(``main.py``) passed the locales, so the pipeline test the user approved the
night before sounded right, and only the scripted live re-render did not. It was
the second time: ``rebuild_lessons_from_story.py`` had already been fixed for
the same omission (tunatale-rag.4).
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.audio.renderer import build_lesson_renderer
from app.config import settings
from app.languages import get_phoneme_planner, get_preprocessor, get_tts_locale, known_language_codes

_BACKEND = Path(__file__).resolve().parent.parent


def _rendered_codes() -> list[str]:
    """Languages the renderer can take: ones with a preprocessor (not the L1 "en")."""
    codes = []
    for code in sorted(known_language_codes()):
        try:
            get_preprocessor(code)
        except ValueError:
            continue
        codes.append(code)
    return codes


# The one sanctioned constructor call, and a Norwegian slicing A/B experiment
# that deliberately renders WITHOUT planners (it measures slicing alone).
_ALLOWED = {
    _BACKEND / "app" / "audio" / "renderer.py",
    _BACKEND / "scripts" / "render_slicing_ab.py",
}


class TestBuildLessonRenderer:
    @pytest.mark.parametrize("code", _rendered_codes())
    def test_declares_each_languages_tts_locale(self, code):
        renderer = build_lesson_renderer(MagicMock(), [code], settings)
        locale = get_tts_locale(code)
        assert renderer._tts_locales == ({code: locale} if locale else {})

    def test_tagalog_plain_text_lines_are_declared_filipino(self):
        """The live defect itself: no locale means no <lang> wrapper."""
        renderer = build_lesson_renderer(MagicMock(), ["no", "tl"], settings)
        assert renderer._tts_locales["tl"] == "fil-PH"
        assert renderer._tts_locales["no"] == "nb-NO"

    @pytest.mark.parametrize("code", _rendered_codes())
    def test_wires_the_languages_phoneme_planner(self, code):
        renderer = build_lesson_renderer(MagicMock(), [code], settings)
        assert (code in renderer._phoneme_planners) == (get_phoneme_planner(code) is not None)

    def test_preprocessor_per_language_and_delivery_settings(self):
        renderer = build_lesson_renderer(MagicMock(), ["no", "tl"], settings)
        assert set(renderer._preprocessors) == {"no", "tl"}
        assert renderer._delivery_codec == settings.audio_delivery_codec
        assert renderer._delivery_bitrate == settings.audio_delivery_bitrate

    def test_slicers_pass_through_and_default_empty(self):
        tts = MagicMock()
        assert build_lesson_renderer(tts, ["no"], settings)._slicers == {}
        slicer = MagicMock()
        assert build_lesson_renderer(tts, ["no"], settings, slicers={"no": slicer})._slicers == {"no": slicer}


def _constructs_renderer(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name == "LessonRenderer":
                return True
    return False


def test_nothing_outside_the_builder_constructs_a_lesson_renderer():
    """A hand-built renderer silently drops whatever kwarg it forgets."""
    offenders = sorted(
        str(p.relative_to(_BACKEND))
        for root in (_BACKEND / "app", _BACKEND / "scripts")
        for p in root.rglob("*.py")
        # scripts/local/ is gitignored scratch: CI never sees it.
        if p not in _ALLOWED and "local" not in p.relative_to(root).parts[:1] and _constructs_renderer(p)
    )
    assert offenders == []


def test_the_guard_sees_a_hand_built_renderer(tmp_path):
    """Control: the detector fires on the exact shape the live script had."""
    src = tmp_path / "script.py"
    src.write_text("renderer = LessonRenderer(tts=tts, preprocessors={})\n", encoding="utf-8")
    assert _constructs_renderer(src)
    src.write_text("renderer = build_lesson_renderer(tts, ['tl'], settings)\n", encoding="utf-8")
    assert not _constructs_renderer(src)
