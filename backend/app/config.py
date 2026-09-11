"""Application configuration via Pydantic Settings."""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_settings import BaseSettings, SettingsConfigDict

# The installed backend package root (``backend/``), used to anchor mutable-path
# defaults that must NOT follow the process CWD. Deploy P0.1: a container, a
# systemd unit, or a restore drill starts somewhere other than ``backend/``, and
# a CWD-relative default silently splits the writer from the reader.
_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: str = ""
    # Per-language DB (one-DB-per-language isolation). Default is the Slovene DB;
    # switch languages by flipping target_language AND database_url together
    # (e.g. sqlite:///./tunatale_no.db for Norwegian).
    database_url: str = "sqlite:///./tunatale_sl.db"
    # Phase 5 — simultaneous multi-language. When non-empty, the app opens one
    # connection per entry (``{"sl": "sqlite:///./tunatale_sl.db", "no": "…_no.db"}``)
    # and resolves the active one per request from the X-TT-Language header. Empty
    # (the default) = single-language: one connection from ``database_url`` bound to
    # ``target_language``. ``target_language`` is the default when no header is sent.
    database_urls: dict[str, str] = {}
    llm_mode: str = "mock"  # mock | live | record | patch
    # gpt-oss-120b replaces llama-3.3-70b-versatile (deprecated by Groq 2026-06-30).
    # It is a reasoning model — main.py pins reasoning_effort=low via
    # reasoning_params_for_model() so it emits content instead of burning the whole
    # budget on reasoning. Free-tier TPM is 8000; WIDER story gen fits, DEEPER (bigger
    # prompt) can approach the ceiling.
    llm_model: str = "openai/gpt-oss-120b"
    # Groq free-tier daily caps for gpt-oss-120b — the binding limits, but TPD
    # appears in no response header, so TT tallies its own spend (UsageLedger) and
    # the rate-limit UI compares against these numbers. Both are ORGANIZATION-level
    # and PER-MODEL: a second API key buys no extra budget, and changing llm_model
    # changes every number. RPD matters because a burst of tiny completions can
    # hit the request ceiling while the token budget still reads healthy.
    groq_tokens_per_day_limit: int = 200_000
    groq_requests_per_day_limit: int = 1_000
    # Ollama/secondary fallback when Groq fails; default off — failures fail loudly.
    llm_allow_fallback: bool = False
    llm_usage_ledger_path: Path = Path("~/.tunatale/llm_usage.log").expanduser()

    target_language: str = "sl"

    anki_collection_path: Path = Path("~/Library/Application Support/Anki2/Will/collection.anki2").expanduser()
    anki_media_path: Path = Path("~/Library/Application Support/Anki2/Will/collection.media").expanduser()
    anki_deck_name: str = "1. Slovene"
    anki_backup_dir: Path = Path("~/.tunatale/anki-backups").expanduser()
    # Retention cap for the safe_open backup directory. safe_open writes a full
    # ~16 MB collection snapshot on every call; without a cap the directory grows
    # without bound. Keep the N most recent snapshots (~16 MB each); <= 0 disables.
    anki_backup_keep: int = 30
    # Rolling daily backups of the per-language content/SRS DBs (tunatale_sl.db,
    # tunatale_no.db). These hold curricula/lessons + FSRS state that is NOT in
    # Anki, are git-ignored, and had no backup layer until an E2E casing bug
    # wiped the Slovene curricula (2026-06-30, 2026-07-13). Snapshotted once per
    # day at startup into a dir OUTSIDE the repo (an in-repo rm/glob can't reach
    # it); the N most recent daily snapshots are kept. <= 0 disables.
    db_backup_dir: Path = Path("~/.tunatale/db-backups").expanduser()
    db_backup_keep_days: int = 5
    # Pre-migration snapshots, one per schema version ever left behind. A
    # SEPARATE directory from db_backup_dir on purpose: those rotate after
    # db_backup_keep_days, and the snapshot that makes a schema rollback
    # possible has to outlive that window — you learn you need it long after
    # the deploy. Never pruned; see app/storage/db_backup.py.
    migration_backup_dir: Path = Path("~/.tunatale/pre-migration-backups").expanduser()
    # TT's canonical media dir, served at /api/srs/media/{filename} and written
    # by the import side (media/importer.py, anki_sync/import_seed.py) and the
    # add-time vocab path. ONE setting on purpose: this used to be a CWD-relative
    # ``./media`` on the import side while four separate module constants walked
    # __file__ upward on the serve side. They coincided only under the dev CWD.
    # Demonstrated 2026-08-12: with MEDIA_DIR pointed at a restored tree, the
    # media route still served the original bytes from backend/media.
    #
    # Pydantic env overrides do NOT expanduser — ``MEDIA_DIR=~/foo`` is a literal
    # "~". Use absolute paths in env (the container does).
    media_dir: Path = _BACKEND_DIR / "media"
    # Rendered lesson audio. Was not a setting at all before Deploy P0.1 —
    # main.py::lifespan hardcoded ``_BACKEND_DIR / "output/audio"``, which this
    # default reproduces exactly, so a dev with no env change sees no difference.
    audio_dir: Path = _BACKEND_DIR / "output/audio"
    anki_fallback_log: Path = Path("~/.tunatale/logs/anki-fallback.log").expanduser()
    # Durable per-sync soak log: every non-dry sync (CLI or API) appends a
    # SYNC_SOAK heartbeat + one RECOMPUTE_DIVERGENCE line per divergence.
    sync_log: Path = Path("~/.tunatale/logs/sync.log").expanduser()
    # Durable WARNING sink. Everything else on the generation path is volatile:
    # the dev server logs to a tty with no redirect, and /api/llm/activity is a
    # 300-event in-memory ring a --reload empties. bd tunatale-y0bk.6.
    # ⚠️ UTC, unlike sync.log above, which is local — see app/logging_sink.py.
    warning_log: Path = Path("~/.tunatale/logs/warnings.log").expanduser()
    # Where a mock-mode cassette miss is appended, one line each, before it
    # raises. None = off. Only the e2e webServer env sets it, and Playwright's
    # global teardown fails the run on any line: a fail-soft caller (the gloss
    # pass) otherwise swallows the raise and the run stays green (tunatale-1l26.7).
    llm_cassette_miss_log: Path | None = None

    # The browser's equivalent of sync.log. OFF by default and pinned so by a
    # test: this is a write endpoint that appends browser-supplied text to a
    # file, so it is opt-in for a debugging session, never ambient. Exists
    # because a bug that only reproduces on a real phone currently leaves no
    # evidence anywhere — Playwright's tap() cannot reproduce a fingertip losing
    # a hit-test, and a device console dies with the tab.
    client_log: Path = Path("~/.tunatale/logs/client.log").expanduser()
    client_log_enabled: bool = False

    # Peer-sync (anki subprocess) config — see sync_orchestrator.py. Also the
    # master toggle main.py reads (alongside plugin importability) to decide
    # whether to mount app.api.anki.router at all — defaults True to preserve
    # the pre-Stage-4 behavior (the router was mounted unconditionally); set
    # False to run TunaTale with the anki_sync plugin fully disabled.
    tt_collection_path: Path = Path("~/.tunatale/tt_collection.anki2").expanduser()
    sync_enabled: bool = True
    sync_endpoint: str = ""  # "" → AnkiWeb default; else self-host URL
    sync_username: str = ""
    # AnkiWeb password. Prefer the macOS Keychain (see sync_keychain_service); this
    # env/.env value is an override fallback and should normally stay EMPTY (plaintext).
    sync_password: str = ""
    # macOS Keychain generic-password service the AnkiWeb password is stored under
    # (account = sync_username). Store it with:
    #   security add-generic-password -s tunatale-ankiweb -a <username> -w
    sync_keychain_service: str = "tunatale-ankiweb"
    # A file whose entire contents are the AnkiWeb password. The portable source:
    # the macOS `security` binary does not exist on Linux, so a deployed box needs
    # somewhere else to read the secret from (tunatale-pkk). Empty → not configured.
    # Prefer this over `sync_password`, which puts the secret in the environment.
    sync_password_file: str = ""
    # Pin for the anki subprocess (`uv run --with anki==X`). Empty → latest anki.
    # Pinned to match the user's desktop Anki (26.08.1 → PyPI `anki==26.8.1`): the sync
    # subprocess must speak the same sync-protocol and mirror the same scheduler the
    # parity code (see .claude/rules/anki-queue-parity.md, "trust the binary") is tuned
    # to. This spec also drives the peer-sync server (via _anki_with_spec) and the
    # oracle harness, so parity is validated against the same version we sync with.
    # The wheel is abi3 (cp310-abi3, requires_python>=3.10), so it imports on 3.14 fine;
    # bump this in lockstep when you upgrade desktop Anki, and re-run oracle + peer-sync.
    #
    # ⚠️ 26.5 → 26.8.1 was NOT a routine bump (2026-08-31). 26.08.1 adopted the
    # non-decreasing SInc(Hard) short-term formula, so it moved together with
    # `_stability_short_term` and the fsrs-rs-python floor; the parity suite is red
    # if any one of the three moves alone. Two CI sites hardcode this value
    # (.github/workflows/ci.yml, the oracle-parity and peer-sync warm-env steps) —
    # they must move with it, or local and CI ground truth disagree.
    #
    # `ANKI_PKG_VERSION=<version>` overrides this, and the oracle harness reads it
    # through _anki_with_spec, so the whole parity suite can be run against any Anki
    # release without editing anything:
    #   ANKI_PKG_VERSION=26.9 uv run pytest tests/test_parity_*.py --run-oracle
    # That is the cheap way to answer "is this Anki release safe for us".
    anki_pkg_version: str = "26.8.1"
    # Interpreter for the anki driver subprocess. It runs isolated + project-free
    # (--no-project), which escapes the project lock's stale protobuf 4.21.2 (dragged in
    # by the classla+anki extras; no cp314 wheel) — a clean resolve pulls a current
    # protobuf that imports fine on 3.14. Pin to an older Python here only if a future
    # anki/protobuf breaks on the latest.
    anki_subprocess_python: str = "3.14"

    anki_model_name: str = ""
    pixabay_api_key: str = ""
    # How many production-card images to pre-stage in the background after a sync
    # (app.cards.media.prestage). Promotion mints PRODUCTIONS_PER_SYNC=10 per sync,
    # so a larger number here drains the backlog while keeping every mint free of a
    # live fetch. 0 disables pre-staging entirely. This is a LATENCY knob — it does
    # not change how many cards are minted, which is a settled pedagogical pacing
    # decision (2026-08-15), not a performance one.
    prestage_images_limit: int = 20
    #: Words per pass for `prestage_cloze_sentences` (tunatale-keb0). Lower than
    #: the image limit because each candidate is TWO Groq calls (generate, then
    #: judge) rather than one Groq call plus a Pixabay fetch, and because the
    #: population it serves is small and fixed — the ~27 closed-class words whose
    #: own notes carry no clozable example. It is a backlog to drain, not a
    #: stream to keep up with.
    prestage_cloze_limit: int = 10
    # Which TTS adapter renders audio: "azure" (official Azure Speech, the
    # default) or "edge" (the unofficial Edge Read Aloud endpoint, retained as an
    # explicit escape hatch and retired by tunatale-i69). The switch is a human
    # decision — there is NO automatic runtime fallback between them, because a
    # silent mid-render swap would mix two providers' renditions of the "same"
    # voice into one curriculum. See app/audio/tts_factory.py.
    tts_provider: str = "azure"
    # TTS concurrency and pacing. tts_max_concurrent_requests stays at 1: Azure
    # has never been tested above 1, so concurrency is a separate, untested axis
    # (findings-tts-pacing-2026-08-21.md, "Explicitly OUT of scope") — do not
    # touch this constant without its own ladder.
    #
    # tts_min_request_delay_s lowered 0.6 -> 0.2 per
    # findings-tts-pacing-2026-08-21.md: throughput measured 210-220 req/min at
    # 0.2s vs 87 req/min at 0.6s (a day-8 render: ~1 min vs ~2.5 min). This is
    # NOT a 429 fix — a fixed-setting repetition control at 0.2s produced 429
    # counts of [0, 0, 8, 0] across four identical runs, a spread larger than
    # every difference measured BETWEEN settings across a 5x span of delay
    # (4, 0, 0, 1, 0, 0 for 1.0s down to 0.2s). Lowering the delay buys wall-clock
    # time only; it does not and must not be sold as reducing throttling. Safe to
    # lower only because the burst-amplification bug (a throttled request paying
    # no pacing delay and freeing its slot instantly) is fixed first — see
    # app/audio/azure_tts.py::_do_synthesize and app/audio/edge_tts.py::_do_synthesize.
    tts_max_concurrent_requests: int = 1
    tts_min_request_delay_s: float = 0.2
    # Per-provider pacing override, falling back to tts_min_request_delay_s when
    # unset (None). Edge measured ZERO throughput degradation from concurrency
    # 1->10 (findings-tts-pacing-2026-08-21.md item 3) — i.e. Azure's pacing
    # constraint does not apply to it — so the two adapters must not be pinned
    # to the same number. Concurrency intentionally has no per-provider override
    # here; see the "OUT of scope" note above.
    tts_azure_min_request_delay_s: float | None = None
    tts_edge_min_request_delay_s: float | None = None
    # Base of the retry ladder both adapters climb: rung N waits
    # tts_retry_base_delay_s * 2**N, so with MAX_RETRIES = 6 an exhausted clip
    # spends 0.5 * (1+2+4+8+16) = 15.5s in backoff. 0.5 is the spacing
    # MAX_RETRIES was sized against — see app/audio/azure_tts.py::MAX_RETRIES —
    # so lowering it shortens the patience that note argues for, and is a
    # tuning decision, not a free speed-up. Configurable because production
    # builds its adapters through get_tts_service(), which never passed one:
    # the ladder was the only pacing knob no caller could reach.
    tts_retry_base_delay_s: float = 0.5
    # Azure Speech (TTS). Replaces the unofficial Edge Read Aloud endpoint that
    # `edge-tts` talks to — same underlying neural voices, but an official API with
    # terms and a support channel. F0 (free tier) allows 500K chars/month and
    # THROTTLES at the cap rather than billing over it.
    # Both default EMPTY on purpose: a region default would let a missing .env still
    # produce a well-formed call to the wrong datacenter, so the call site must fail
    # loudly instead. Region is the machine-readable form ("eastus"), not "East US" —
    # it is interpolated straight into the endpoint host.
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    # Global lemmatizer gate: "lowercase" (default) forces the deterministic
    # lowercase engine for EVERY language (the CI/test pin, and how a deployment
    # disables the heavy PyTorch pipelines). Any other value ("classla", "stanza",
    # "auto", …) opts in, and the ENGINE is then chosen per language from the
    # registry (app.languages.get_lemmatizer_type: sl→classla, no→stanza). This is
    # per-language, not one-engine-per-process, so multi-language mode
    # (database_urls) analyzes each language with its own model. See get_lemmatizer.
    lemmatizer_type: str = "lowercase"

    anki_new_per_day_default: int = 20
    anki_reviews_per_day_default: int = 200

    # Lesson audio delivery format. Opus is ~10-20× smaller than WAV for speech,
    # cutting mobile-data use when streaming lessons to a phone. Set to "wav" to
    # restore uncompressed delivery. Codec must be a key of transcode.CODEC_EXT.
    audio_delivery_codec: str = "opus"  # opus | aac | mp3 | wav
    audio_delivery_bitrate: str = "28k"

    # Where aligned syllable boundaries are cached between renders, so a re-render
    # never re-runs the model. Boundaries are keyed by (word, voice, rate, model).
    audio_alignment_cache_dir: Path = Path("~/.tunatale/alignment-cache").expanduser()

    # Where synthesized clips are cached BETWEEN renders, keyed by
    # (voice, rate, text) — the sha256 mp3 cache both TTS adapters have always
    # implemented and which nothing in production ever passed a directory to.
    # Wiring it is what makes a failed render resumable: since the burst
    # throttle (fb0a17f) a lesson is a ~24-minute serial run of TTS calls, so
    # one clip failing at minute 23 used to cost all 24 minutes again.
    # Unbounded — nothing evicts from it; see tunatale-rm6v.
    tts_cache_dir: Path = Path("~/.tunatale/tts-cache").expanduser()

    # A render that dies on ONE exhausted clip re-runs itself rather than
    # waiting for a human to notice and press the button again (tunatale-uxm0).
    # The cache above is what makes this converge: every clip that DID
    # synthesize is on disk, so pass 2 only does what pass 1 missed and each
    # pass strictly advances. Bounded, because "try hard" is not "try forever".
    tts_render_max_attempts: int = 3
    # Paid BETWEEN passes, never on the happy path. Throttling is exogenous and
    # driven by load on a shared downstream service
    # (findings-tts-pacing-2026-08-21.md), so an immediate re-run walks straight
    # back into the same window; the cooldown is there to let it pass.
    tts_render_retry_cooldown_s: float = 15.0

    pipeline_autostart: bool = True

    # ── Deployment profile ───────────────────────────────────────────────────
    # "" (or "dev") is the local/Tailscale setup and changes nothing. "prod"
    # arms the startup guard in main.py::lifespan, which REFUSES to boot rather
    # than serve a misconfigured deployment. See prod_profile_problems below.
    tt_env: str = ""

    # Browser origins allowed to call the API cross-origin.
    #
    # This is deliberately NOT a wildcard. The app has no authentication, so
    # `allow_origins=["*"]` (what shipped until this setting existed) meant any
    # page loaded in any browser that could reach the server — localhost, or the
    # MagicDNS name from a tailnet device — could read and write TunaTale data.
    #
    # The normal flows never need these entries at all: the browser talks only to
    # Vite on :5173, which proxies /api to :8000 server-side (frontend/vite.config.ts),
    # and a production build is served same-origin behind Caddy. They exist for
    # direct-to-:8000 use (the /docs "Try it out" console, a curl-alike in a page).
    # ⚠️ pydantic parses a list field from JSON, not CSV: CORS_ORIGINS=["https://x"].
    cors_origins: list[str] = ["http://localhost:5173", "https://localhost:5173"]
    # For origins that can't be enumerated — a MagicDNS name is per-tailnet, so it
    # belongs in a scoped pattern rather than in a literal nobody will update:
    #   CORS_ALLOW_ORIGIN_REGEX=^https://[a-z0-9-]+\.[a-z0-9-]+\.ts\.net:5173$
    # Empty = unset. It must never reach Starlette as "", which compiles to a
    # regex matching every origin — main.py::cors_kwargs drops it.
    cors_allow_origin_regex: str = ""

    # Phase 1 gives these teeth (require_user, server-side sessions). They land
    # here as an inert default-False so the prod guard can assert on them now,
    # rather than gating a live CORS fix behind the whole auth phase.
    auth_enabled: bool = False
    session_secret: str = ""

    # Identity lives in its OWN database. The content DBs are per-language
    # (tunatale_sl.db / tunatale_no.db) and get copied, migrated and restored
    # per language, so a users table inside one of them would exist once per
    # language and disagree with itself. Sits alongside them by default.
    auth_database_url: str = "sqlite:///./auth.db"
    # Default lifetime of a login session, read by
    # app.auth.database.AuthDatabase.create_session. P1.2 rotates the token on
    # login rather than extending an existing session.
    session_ttl_days: int = 30
    # Trusted proxy header for client-IP resolution.  Empty means "read the
    # socket peer", which is right for direct exposure and for local dev.
    # Behind the Caddy reverse proxy the socket peer is the proxy, so every
    # user in the world would share one throttle bucket and the per-IP limit
    # would be worse than useless — set it to X-Forwarded-For there.  See
    # app.auth.throttle.client_ip.
    trusted_proxy_header: str = ""
    # The zone the PROCESS keeps, read from the standard `TZ` rather than a
    # TT_-prefixed name because libc is the real consumer: `rollover.py`
    # resolves the study day with a bare `.astimezone()`. Empty is correct for
    # dev (the laptop's own zone is already right) and is refused under
    # TT_ENV=prod, where the container inherits nothing from its host and the
    # fallback — UTC — moves ANKI_ROLLOVER_HOUR away from the user's 4 AM.
    tz: str = ""


def prod_profile_problems(s: Settings) -> list[str]:
    """Everything wrong with *s* as a production profile, as human sentences.

    Pure and profile-agnostic: it does NOT consult ``tt_env``, so both callers
    can decide for themselves when the rules apply — ``main.py`` arms it only on
    ``TT_ENV=prod``, while ``scripts/check_prod_env.py`` applies it to a file
    that claims to be one. Returns every problem at once; a deploy that fails
    one restart per mistake is a deploy nobody finishes.
    """
    problems: list[str] = []
    if s.llm_mode != "live":
        problems.append(
            f"llm_mode is {s.llm_mode!r}, not 'live' — the app would serve recorded"
            " cassette replies and look healthy doing it (set LLM_MODE=live)"
        )
    if not s.auth_enabled:
        problems.append(
            "auth_enabled is False — the API would be open to anyone who can reach it (set AUTH_ENABLED=true)"
        )
    if not s.session_secret:
        problems.append("session_secret is unset — sessions cannot be signed (set SESSION_SECRET)")
    if "*" in s.cors_origins:
        problems.append("cors_origins contains '*' — a wildcard origin on a credentialed API (set an explicit list)")
    if s.cors_allow_origin_regex.strip() in {".*", "^.*$", ".+"}:
        problems.append("cors_allow_origin_regex matches every origin — scope it to the hosts you actually serve")
    if not s.cors_origins and not s.cors_allow_origin_regex:
        problems.append(
            "cors_origins is empty and no cors_allow_origin_regex is set — no browser client could reach the API"
        )
    if s.auth_enabled and not s.trusted_proxy_header:
        problems.append(
            "trusted_proxy_header is unset — behind the reverse proxy every request"
            " appears to come from the proxy, so login throttling would treat all"
            " callers as one client (set TRUSTED_PROXY_HEADER=X-Forwarded-For)"
        )
    problems += _zone_problems(s)
    return problems


def _zone_problems(s: Settings) -> list[str]:
    """Whether ``tz`` NAMES a usable zone. Not whether the process keeps it."""
    if not s.tz:
        return [
            "tz is unset — a container inherits no zone from its host, so the"
            " process would resolve local time as UTC and ANKI_ROLLOVER_HOUR"
            " (4 AM local) would fire at 04:00 UTC, putting the study-day"
            " boundary hours away from where Anki puts it on the user's own"
            " devices (set TZ to an IANA zone, e.g. TZ=America/New_York)"
        ]
    try:
        ZoneInfo(s.tz)
    except ZoneInfoNotFoundError, ValueError:
        return [
            f"tz is {s.tz!r}, which this process cannot resolve to a zone —"
            " either it is not an IANA name (use 'America/New_York', not 'EST')"
            " or the image has no zone data installed"
        ]
    return []


def clock_runtime_problems(s: Settings, now: datetime | None = None) -> list[str]:
    """Whether the RUNNING PROCESS actually keeps the zone ``tz`` names.

    Separate from ``prod_profile_problems`` because it is not a property of the
    settings: it reads the live clock, so only the box can answer it and
    ``scripts/check_prod_env.py`` deliberately cannot.

    The failure it catches is silent by construction.
    ``app.srs.anki_mirror.rollover._local_now`` resolves the local day with a
    bare ``.astimezone()``, which asks **libc** — and libc, handed a ``TZ`` it
    has no zone data for, does not raise. It falls back to UTC. So ``TZ`` is
    set, the name is a real zone, ``zoneinfo`` resolves it happily from the
    bundled ``tzdata`` wheel, and every study-day boundary is still in the
    wrong place. Comparing the two resolutions is what separates them.

    Says nothing when ``tz`` is unset or unresolvable: those are
    ``_zone_problems``' to report, and one boot should not print the same
    misconfiguration twice.
    """
    if not s.tz:
        return []
    try:
        zone = ZoneInfo(s.tz)
    except ZoneInfoNotFoundError, ValueError:
        return []

    now = now or datetime.now()
    process_offset = now.astimezone().utcoffset()
    named_offset = now.replace(tzinfo=zone).utcoffset()
    if process_offset != named_offset:
        return [
            f"TZ={s.tz} names a zone at UTC{_offset_text(named_offset)}, but this"
            f" process resolves local time as UTC{_offset_text(process_offset)}."
            " libc is not reading that zone — the image is probably missing"
            " /usr/share/zoneinfo (install tzdata). Every SRS day boundary would"
            " be silently wrong."
        ]
    return []


def _offset_text(offset: timedelta | None) -> str:
    """``+02:00`` / ``-05:00`` — for a message a human has to act on at 3 AM."""
    total = int((offset or timedelta()).total_seconds())
    sign = "-" if total < 0 else "+"
    hours, remainder = divmod(abs(total), 3600)
    return f"{sign}{hours:02d}:{remainder // 60:02d}"


settings = Settings()


# Anki rolls the study day over at this *local* hour (default 4 AM), not at
# midnight — a grade timestamped between local midnight and the rollover belongs
# to the PRIOR Anki day. The rollover arithmetic is single-sourced in
# `app.srs.anki_mirror.rollover` (local-day domain: `local_today_rollover`,
# `anki_day_bounds_utc`, `anki_today`; due_at convention: `due_at_rollover_utc`);
# `app.srs.anki_mirror.protobuf_wire` owns the separate col-day index domain
# (`compute_anki_day_index`, `review_due_at_for_col_day`). Both derive from this
# constant. Promote to a Settings field if it ever needs to be config-driven
# (Anki stores it per-collection).
ANKI_ROLLOVER_HOUR = 4
