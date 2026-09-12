"""Missing-cassette failure mode (tunatale-kbb.11).

The production image ships no ``backend/tests/``, so booting it with
``llm_mode != live`` died inside lifespan with a bare
``FileNotFoundError: [Errno 2] No such file or directory: '/app/tests/cassettes/e2e.json'``
— a path with no cause. The image was right and the CONFIG was wrong, but the
traceback said neither. Cassette-backed modes must fail with an error that names
the mode and the path and says the file is absent.
"""

from pathlib import Path

import pytest

from app.llm.cassette import CASSETTE_VERSION, CassetteLLMClient

# A real, version-current cassette shipped with the tests — the control that an
# existing valid cassette still loads exactly as before.
_REAL_CASSETTE = Path(__file__).parent / "cassettes" / "TestPlannerLLM__test_two_turn_scenario.json"


@pytest.mark.parametrize("mode", ["mock", "patch"])
def test_cassette_backed_mode_with_absent_file_names_mode_and_path(tmp_path, mode):
    """mock/patch cannot boot without the cassette; the error must say so.

    The old code let ``read_text`` FileNotFoundError through, which named the
    path but not why it matters. The new error is raised in that error's place.
    """
    path = tmp_path / "cassettes" / "not-on-disk.json"

    with pytest.raises(FileNotFoundError) as excinfo:
        CassetteLLMClient(mode=mode, cassette_path=path)

    msg = str(excinfo.value)
    assert mode in msg
    assert str(path) in msg


def test_live_mode_constructs_fine_with_absent_cassette(tmp_path):
    """live never reads a cassette — a fix that refuses on every mode breaks production."""
    path = tmp_path / "cassettes" / "not-on-disk.json"
    client = CassetteLLMClient(mode="live", cassette_path=path)
    assert client is not None


def test_existing_cassette_still_loads_in_mock_mode():
    """The control: an existing, valid cassette loads in mock mode exactly as before."""
    assert _REAL_CASSETTE.exists()
    client = CassetteLLMClient(mode="mock", cassette_path=_REAL_CASSETTE)
    assert client._playback_by_hash


def test_existing_cassette_with_wrong_version_still_raises_runtime_error(tmp_path):
    """A cassette present but the wrong schema version keeps its own RuntimeError.

    The new missing-file check must fire on ABSENCE only — a wrong-version file
    exists, so it must still raise the version-mismatch RuntimeError, not the
    new missing-file error.
    """
    path = tmp_path / "stale.json"
    path.write_text(f'{{"version": {CASSETTE_VERSION - 1}, "calls": []}}')

    with pytest.raises(RuntimeError, match="expected"):
        CassetteLLMClient(mode="mock", cassette_path=path)
