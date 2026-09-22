"""ContentStore must refuse a sqlite URL where it wants a filesystem path (tunatale-zjjo).

Measured 2026-09-22: twice (2026-08-19, 2026-09-12) something handed
ContentStore a `sqlite:///...` URL. It is the one DB constructor that does
Path(db_path).parent.mkdir() + sqlite3.connect() with no URL handling, so it
silently created `backend/sqlite:/.../tunatale_no.db` and read/wrote an empty
throwaway store instead of the real one. A BP sweep found no tracked caller
that does this; the producer was an ad-hoc script. So the defence belongs at
the sink, and it must be LOUD: a silent strip would hide the caller's bug.
"""

import pytest

from app.storage.store import ContentStore


@pytest.mark.parametrize(
    "url",
    ["sqlite:///tunatale_no.db", "sqlite:///./tunatale_no.db", "sqlite:////abs/path/tunatale_no.db"],
)
def test_a_sqlite_url_is_refused_and_creates_nothing(tmp_path, monkeypatch, url):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="resolve_db_path"):
        ContentStore(url)
    assert not (tmp_path / "sqlite:").exists()  # the artifact it used to leave


def test_a_plain_path_still_works(tmp_path):
    store = ContentStore(str(tmp_path / "sub" / "content.db"))
    store.close()
    assert (tmp_path / "sub" / "content.db").exists()
