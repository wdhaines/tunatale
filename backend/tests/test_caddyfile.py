"""The Caddyfile's security-relevant properties, pinned as text (tunatale-1oq).

Nothing in `./test.sh` or CI runs Caddy, so these are the only guard the file
has. The runtime claim — that Caddy accepts the file and serves a trusted
certificate — is verified on the box with `caddy validate` and a real request,
and recorded in docs/deployment.md; this module does not pretend to make it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CADDYFILE = Path(__file__).resolve().parents[2] / "Caddyfile"


@pytest.fixture(scope="module")
def text() -> str:
    return CADDYFILE.read_text(encoding="utf-8")


def test_the_site_address_is_the_env_var_with_a_plain_http_default(text):
    """`{$SITE_ADDRESS::80}`: the box sets the domain and gets automatic HTTPS; a
    laptop sets nothing and gets the plain :80 it always had. A hardcoded domain
    would make every local `docker compose up` try to obtain a certificate."""
    first_block = next(line for line in text.splitlines() if line.rstrip().endswith("{"))
    assert first_block.strip() == "{$SITE_ADDRESS::80} {", first_block


def test_the_api_is_matched_before_the_spa_fallback(text):
    """The classic misorder: if the SPA's `try_files ... /index.html` handles
    /api/* first, an unknown API path returns 200 and the HTML shell instead of
    the API's 404, and every client error reads as success."""
    assert text.index("handle /api/*") < text.index("try_files")


@pytest.mark.parametrize(
    "header",
    [
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Permissions-Policy",
    ],
)
def test_security_headers_are_set(text, header):
    assert re.search(rf"^\s*{re.escape(header)}\s", text, re.MULTILINE), f"{header} is not set"


def _csp_directives(text) -> dict[str, list[str]]:
    csp = re.search(r'Content-Security-Policy\s+"([^"]+)"', text).group(1)
    return {d.split()[0]: d.split()[1:] for d in (p.strip() for p in csp.split(";")) if d}


def test_the_csp_admits_only_this_origin(text):
    """`connect-src 'self'` is what stops a successful injection from shipping
    data anywhere, and `frame-ancestors 'none'` stops the app being framed for
    clickjacking. The one cross-origin load is below."""
    directives = _csp_directives(text)
    assert directives["default-src"] == ["'self'"]
    assert directives["connect-src"] == ["'self'"]
    assert directives["frame-ancestors"] == ["'none'"]
    assert directives["object-src"] == ["'none'"]
    hosts = {name: [v for v in values if "http" in v] for name, values in directives.items()}
    assert {name: v for name, v in hosts.items() if v} == {"img-src": ["https://cdn.pixabay.com"]}


def test_the_image_picker_can_show_pixabay_thumbnails(text):
    """ImageEditModal renders each candidate as <img src={c.preview_url}>, and
    Pixabay serves previewURL from cdn.pixabay.com (checked against the live API
    2026-09-18). Without it in img-src every thumbnail is blocked: the picker
    shipped broken on prod the day the CSP went on, because the comment above
    the policy claimed the frontend loads nothing cross-origin."""
    assert "https://cdn.pixabay.com" in _csp_directives(text)["img-src"]


def test_request_bodies_are_capped_above_the_upload_limit(text):
    """The API rejects image uploads over 10 MB itself (srs_images.py
    _MAX_UPLOAD_BYTES); the proxy cap sits just above it so a legitimate upload
    still reaches the API's own, more informative, error."""
    match = re.search(r"max_size\s+(\d+)MB", text)
    assert match, "no request_body max_size"
    assert 10 < int(match.group(1)) <= 20, match.group(0)
