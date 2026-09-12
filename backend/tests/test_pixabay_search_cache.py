"""24h TTL cache for Pixabay search results (tunatale-kbb.12).

Pixabay's API terms require search RESULTS be cached 24h rather than
re-requested. The cache lives in front of the API layer (:func:`search_pixabay`),
so the image picker and the add-card path share it. The counter on the faked
``httpx.Client`` is the oracle: a cache hit must make zero outbound requests.
"""

import httpx
import pytest

from app.cards.media import pixabay
from app.cards.media.pixabay import PixabaySearch, fetch_pixabay_image, search_pixabay
from app.config import settings


def _hit(tag: str) -> dict:
    return {
        "tags": tag,
        "webformatURL": f"https://cdn.pixabay.com/{tag}.jpg",
        "imageType": "photo",
    }


class _FakeClock:
    """Controllable substitute for :func:`pixabay._search_cache_now`."""

    def __init__(self) -> None:
        self.now_t = 0.0

    def now(self) -> float:
        return self.now_t


class _CallQueue:
    """Serves the next configured search result and counts outbound requests."""

    def __init__(self) -> None:
        self._results: list[tuple[str, list[dict]]] = []
        self.search_requests = 0

    def enqueue(self, status: str, hits: list[dict] | None = None) -> None:
        self._results.append((status, hits or []))

    def next_search(self) -> tuple[str, list[dict]]:
        self.search_requests += 1
        return self._results.pop(0)


class _FakeClient:
    """Fake ``httpx.Client``: search URLs hit the queue, downloads return bytes."""

    def __init__(self, queue: _CallQueue) -> None:
        self._queue = queue

    def get(self, url, *, params=None, timeout=None):
        if pixabay._PIXABAY_API in url:
            status, hits = self._queue.next_search()
            if status == "rate_limited":
                raise httpx.HTTPStatusError(
                    "429 Too Many Requests",
                    request=httpx.Request("GET", url),
                    response=httpx.Response(429),
                )
            if status == "api_error":
                raise httpx.TransportError("mock outage")
            return httpx.Response(200, json={"hits": hits, "totalHits": len(hits)}, request=httpx.Request("GET", url))
        return httpx.Response(200, content=b"\xff\xd8fake-jpeg", request=httpx.Request("GET", url))

    def close(self) -> None:
        pass


class _SearchSeam:
    """Wires the fake client, fake clock and test-key into the module."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.queue = _CallQueue()
        self.clock = _FakeClock()
        monkeypatch.setattr("app.cards.media.pixabay.httpx.Client", lambda: _FakeClient(self.queue))
        monkeypatch.setattr(pixabay, "_search_cache_now", self.clock.now)
        monkeypatch.setattr(settings, "pixabay_api_key", "test-key")

    def search(self, query: str, *, per_page: int = 50) -> PixabaySearch:
        return search_pixabay(query, api_key="test-key", per_page=per_page)


@pytest.fixture(autouse=True)
def _clear_search_cache():
    pixabay._search_cache.clear()
    yield
    pixabay._search_cache.clear()


@pytest.fixture
def seam(monkeypatch: pytest.MonkeyPatch) -> _SearchSeam:
    return _SearchSeam(monkeypatch)


def test_same_word_twice_is_one_request(seam):
    """The bead's decisive oracle: opening the picker twice for one word is one API call."""
    seam.queue.enqueue("ok", [_hit("tree")])
    first = seam.search("tree")
    second = seam.search("tree")
    assert seam.queue.search_requests == 1
    assert first.hits == second.hits == [_hit("tree")]


def test_two_different_words_are_two_requests(seam):
    """The control: a cache that returns word A's hits for word B is worse than no cache."""
    seam.queue.enqueue("ok", [_hit("tree")])
    seam.queue.enqueue("ok", [_hit("cat")])
    first = seam.search("tree")
    second = seam.search("cat")
    assert seam.queue.search_requests == 2
    assert first.hits == [_hit("tree")]
    assert second.hits == [_hit("cat")]


def test_past_ttl_is_a_fresh_request(seam):
    ttl = pixabay._SEARCH_CACHE_TTL_SECONDS
    seam.queue.enqueue("ok", [_hit("tree")])
    seam.queue.enqueue("ok", [_hit("tree")])
    seam.search("tree")
    seam.clock.now_t = ttl + 1  # just past the window; no sleeping 24h
    result = seam.search("tree")
    assert seam.queue.search_requests == 2
    assert result.status == "ok"


def test_just_inside_ttl_is_still_cached(seam):
    ttl = pixabay._SEARCH_CACHE_TTL_SECONDS
    seam.queue.enqueue("ok", [_hit("tree")])
    seam.search("tree")
    seam.clock.now_t = ttl - 1
    result = seam.search("tree")
    assert seam.queue.search_requests == 1
    assert result.status == "ok"


@pytest.mark.parametrize("bad_status", ["rate_limited", "api_error", "no_results"])
def test_failed_or_empty_result_is_not_cached_then_success_repopulates(seam, bad_status):
    """A failed/empty search must not suppress a real search for 24 hours."""
    seam.queue.enqueue(bad_status)
    seam.queue.enqueue("ok", [_hit("tree")])
    first = seam.search("tree")
    second = seam.search("tree")
    assert first.status == bad_status
    assert seam.queue.search_requests == 2
    assert second.status == "ok"
    assert second.hits == [_hit("tree")]


def test_differing_per_page_is_two_requests(seam):
    """per_page changes the result set, so it is part of the cache key."""
    seam.queue.enqueue("ok", [_hit("tree")])
    seam.queue.enqueue("ok", [_hit("tree")])
    seam.search("tree", per_page=50)
    seam.search("tree", per_page=25)
    assert seam.queue.search_requests == 2


@pytest.mark.parametrize(
    ("q1", "q2"),
    [
        ("tree", "Tree"),
        ("tree", "  tree  "),
        ("båt", "øl"),
        ("x" * 500, "x" * 499),
        ("", "tree"),
    ],
    ids=["case", "whitespace", "unicode", "long", "empty"],
)
def test_distinct_query_strings_are_distinct_cache_entries(seam, q1, q2):
    """The key is the raw query string: similarities must never cross-pollute."""
    seam.queue.enqueue("ok", [_hit("q1")])
    seam.queue.enqueue("ok", [_hit("q2")])
    r1 = seam.search(q1)
    r1_again = seam.search(q1)
    r2 = seam.search(q2)
    assert r1.hits == r1_again.hits == [_hit("q1")]
    assert r2.hits == [_hit("q2")]
    assert seam.queue.search_requests == 2


def test_two_fetches_for_one_term_make_one_request(seam):
    """The add-card path must POPULATE the cache, not only read it.

    tunatale-kbb.12's whole point is Pixabay's 24h caching term. fetch is real
    production traffic (pixabay.py::fetch_pixabay_image passes its own
    http_client), so if it never populates, repeated card creation for the same
    term re-requests every time and the term is not satisfied.
    """
    seam.queue.enqueue("ok", [_hit("tree")])
    seam.queue.enqueue("ok", [_hit("tree")])
    assert fetch_pixabay_image("tree", api_key="test-key") is not None
    assert fetch_pixabay_image("tree", api_key="test-key") is not None
    assert seam.queue.search_requests == 1


def test_fetch_pixabay_image_shares_the_search_cache(seam):
    """The add-card path (fetch) must read the picker's cached search, not re-request."""
    seam.queue.enqueue("ok", [_hit("tree")])
    picked = seam.search("tree")  # picker populates the cache
    assert picked.status == "ok"

    image = fetch_pixabay_image("tree", api_key="test-key")
    assert image is not None
    assert image[0] == b"\xff\xd8fake-jpeg"
    assert seam.queue.search_requests == 1  # fetch reused the cached result set
