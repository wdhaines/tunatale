"""The repicture_shared_photos CLI: dry run by default, --apply writes, --db picks the learner."""

from __future__ import annotations

from types import SimpleNamespace

from app.cards.media import vocab_media
from app.common.guid import compute_guid
from app.config import settings
from app.models.srs_item import Direction
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from scripts.repicture_shared_photos import _sync_media_fn, main


def _mothers(url: str) -> SRSDatabase:
    """nanay (zipf 4.39) and inahan (5.45) on one photo, both with Anki notes."""
    db = SRSDatabase(url)
    for i, text in enumerate(("nanay", "inahan")):
        db.add_collocation(
            SyntacticUnit(text=text, translation="mother", word_count=1, difficulty=1, source="corpus"), "ceb"
        )
        guid = compute_guid(text, "ceb", "")
        db.set_anki_ids(guid, 7001 + i, {Direction.RECOGNITION: 70010 + i})
        vocab_media.store_tt_media(db, db.get_collocation_id_by_guid(guid), "image", f"img_{text}.jpg", b"SHARED")
    return db


def _image(db: SRSDatabase, text: str) -> str | None:
    return db.get_image_filename(db.get_collocation_id_by_guid(compute_guid(text, "ceb", "")))


def test_dry_run_prints_the_plan_and_writes_nothing(tmp_path, monkeypatch, capsys) -> None:
    url = f"sqlite:///{tmp_path / 'ceb.db'}"
    monkeypatch.setattr(settings, "database_urls", {"ceb": url})
    db = _mothers(url)

    assert main(["--language", "ceb"]) == 0

    out = capsys.readouterr().out
    assert "keeps: 2 inahan 'mother' zipf 5.45" in out
    assert "re-picture: 1 nanay 'mother' zipf 4.39 (push)" in out
    assert "1 shared photo(s), 1 card(s) to re-picture, 0 tie(s), 1 LLM image quer(ies) not cached." in out
    assert _image(db, "nanay") == "img_nanay.jpg"


async def _fresh(word, english, *, used_image_urls, source_sentence="", grammar="", audio="full"):
    return SimpleNamespace(image_bytes=b"FRESH-" + word.encode(), image_url=f"https://px/{word}", image_ext="jpg")


async def _nothing(word, english, **_kwargs):
    return None


def test_apply_against_an_explicit_learner_db(tmp_path, capsys) -> None:
    url = f"sqlite:///{tmp_path / 'learner2_ceb.db'}"
    db = _mothers(url)

    assert main(["--language", "ceb", "--db", url, "--apply"], _media_fn=_fresh) == 0

    out = capsys.readouterr().out
    assert "  new    1 nanay: img_nanay.jpg -> img_mother_" in out
    assert "Re-pictured 1 of 1." in out
    assert _image(db, "nanay").startswith("img_mother_")
    assert _image(db, "inahan") == "img_inahan.jpg"


def test_apply_reports_a_card_that_kept_its_photo(tmp_path, capsys) -> None:
    url = f"sqlite:///{tmp_path / 'ceb.db'}"
    db = _mothers(url)

    assert main(["--language", "ceb", "--db", url, "--apply"], _media_fn=_nothing) == 0

    out = capsys.readouterr().out
    assert "  kept   1 nanay: no picture found" in out
    assert "Re-pictured 0 of 1." in out
    assert _image(db, "nanay") == "img_nanay.jpg"


def test_a_tie_is_labelled(tmp_path, capsys) -> None:
    url = f"sqlite:///{tmp_path / 'ceb.db'}"
    db = SRSDatabase(url)
    for text in ("qqa", "qqb"):  # neither is in the frequency table
        db.add_collocation(
            SyntacticUnit(text=text, translation="x", word_count=1, difficulty=1, source="corpus"), "ceb"
        )
        coll_id = db.get_collocation_id_by_guid(compute_guid(text, "ceb", ""))
        vocab_media.store_tt_media(db, coll_id, "image", f"{text}.jpg", b"SAME")

    assert main(["--language", "ceb", "--db", url]) == 0

    out = capsys.readouterr().out
    assert "(tie: oldest keeps)" in out and "(no Anki note)" in out


def test_the_live_media_generator_is_the_syncs_own(tmp_path) -> None:
    # Built, not called: calling it would reach Groq and Pixabay.
    fn = _sync_media_fn(SRSDatabase(f"sqlite:///{tmp_path / 'ceb.db'}"), "ceb")
    assert fn.__qualname__ == "_build_media_fn.<locals>._media_fn"
