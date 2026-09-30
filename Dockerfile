# ── Stage 1: build the SvelteKit frontend ─────────────────────────────────────
FROM oven/bun:1-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
RUN bun run build

# ── Stage 2: production Python API ───────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.14-bookworm AS api

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-group slovene --no-group norwegian --no-group alignment

COPY backend/app ./app

# Build every built-not-committed data file now (the lemma tables' SQLite
# indexes AND the NST lexicon), so the running container never writes into /app
# (appuser does not own it) and the first request is not slow. The lexicon was
# missing from every image before app.build_data existed (tunatale-ip8q).
RUN /app/.venv/bin/python -m app.build_data

RUN useradd --home-dir /data --no-create-home appuser \
    && chown appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["/app/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── Stage 3: Caddy reverse-proxy + static SPA ───────────────────────────────
FROM caddy:2-alpine AS web

COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend-build /app/build /srv

EXPOSE 80 443 443/udp
