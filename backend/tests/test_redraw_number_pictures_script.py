"""The redraw_number_pictures CLI: dry run by default, --apply writes, --db picks the learner."""

from __future__ import annotations

from app.cards.media import vocab_media
from app.cards.number_picture import number_picture
from app.config import settings
from app.models.srs_item import Direction
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from scripts.redraw_number_pictures import main


def _photographed_duha(url: str) -> SRSDatabase:
    db = SRSDatabase(url)
    db.add_collocation(
        SyntacticUnit(text="duha", translation="two", word_count=1, difficulty=1, source="corpus"), "ceb"
    )
    item = db.get_collocation("duha")
    db.set_anki_ids(item.guid, 7001, {Direction.RECOGNITION: 70010})
    vocab_media.store_tt_media(db, db.get_collocation_id_by_guid(item.guid), "image", "img_two_1234abcd.jpg", b"PHOTO")
    return db


def test_dry_run_prints_the_plan_and_writes_nothing(tmp_path, monkeypatch, capsys) -> None:
    url = f"sqlite:///{tmp_path / 'ceb.db'}"
    monkeypatch.setattr(settings, "database_urls", {"ceb": url})
    db = _photographed_duha(url)

    assert main(["--language", "ceb"]) == 0

    out = capsys.readouterr().out
    assert "duha" in out and "img_two_1234abcd.jpg" in out and "(push)" in out
    assert "1 card(s) to redraw." in out
    assert (
        db.get_image_filename(db.get_collocation_id_by_guid(db.get_collocation("duha").guid)) == "img_two_1234abcd.jpg"
    )


def test_apply_against_an_explicit_learner_db(tmp_path, capsys) -> None:
    url = f"sqlite:///{tmp_path / 'learner2_ceb.db'}"
    db = _photographed_duha(url)

    assert main(["--language", "ceb", "--db", url, "--apply"]) == 0

    assert "Redrew 1." in capsys.readouterr().out
    coll_id = db.get_collocation_id_by_guid(db.get_collocation("duha").guid)
    assert db.get_image_filename(coll_id) == number_picture("duha", "ceb").filename
