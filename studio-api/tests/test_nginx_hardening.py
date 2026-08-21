"""Guard the edge hardening in docker/nginx/default.conf and supervisord.conf.

Two properties here are one careless edit away from a security regression, and
neither is visible from inside the Python app:

1. **Header ownership.** `X-Forwarded-Prefix` becomes Next's `<base href>` and
   `X-Forwarded-Host` is what `security.py` compares the browser's `Origin`
   against. If nginx doesn't set them, a client can. The trap is that
   `proxy_set_header` is *not* inherited into a location that declares any
   `proxy_set_header` of its own — and every location here does — so the
   hygiene lines have to be repeated in every block, and a new block added
   later silently starts passing client headers through.
2. **`add_header` has the same inheritance rule.** The security headers are
   declared once at `server` level; a stray `add_header` inside a location
   drops the whole inherited set for that location. The two Jupyter blocks do
   that deliberately (Jupyter sets its own CSP) and re-add the two headers that
   are safe there — no other location may.

Nothing in the Python suite can run nginx, so these assert the shape of the
config, the way tests/test_nginx_prefix_routing.py does for the routing.
"""

from __future__ import annotations

import configparser
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONF = os.path.join(ROOT, "docker", "nginx", "default.conf")
SUPERVISORD = os.path.join(ROOT, "docker", "supervisor", "supervisord.conf")
URLS_TS = os.path.join(ROOT, "studio-web", "lib", "urls.ts")
LAYOUT_TSX = os.path.join(ROOT, "studio-web", "app", "layout.tsx")
CHAT_EXT = os.path.join(ROOT, "jupyter-pegasusai-chat", "src", "index.ts")

# The four headers every proxying location must set for itself.
FORWARDED_HEADERS = (
    r"proxy_set_header\s+X-Forwarded-Prefix\s+",
    r'proxy_set_header\s+X-Script-Name\s+"";',
    r"proxy_set_header\s+X-Forwarded-Host\s+\$http_host;",
    r'proxy_set_header\s+X-Auth-User\s+"";',
)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _directives() -> str:
    """default.conf with its comments removed.

    The comments here quote the directives they explain ("a second CSP …"), so
    matching against the raw file would let a comment satisfy — or trip — an
    assertion about what nginx is actually configured to send.
    """
    return re.sub(r"#[^\n]*", "", _read(CONF))


def _brace_span(text: str, start: int) -> int:
    """Index just past the `}` closing the block whose `{` is at start - 1."""
    depth, i = 1, start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return i


def _location_blocks(text: str) -> list[tuple[str, str, int, int]]:
    """(location line, body, start offset, end offset) for every location."""
    out = []
    for m in re.finditer(r"^[ \t]*location[^\n]*\{", text, re.M):
        end = _brace_span(text, m.end())
        out.append((m.group(0).strip(), text[m.end() : end], m.start(), end))
    return out


def _proxied_blocks() -> list[tuple[str, str]]:
    return [
        (loc, body)
        for loc, body, _, _ in _location_blocks(_directives())
        if "proxy_pass" in body
    ]


def _server_level(text: str) -> str:
    """The server block's own directives, with every location block removed."""
    m = re.search(r"^server\s*\{", text, re.M)
    assert m, "no server block in default.conf"
    body = text[m.end() : _brace_span(text, m.end())]
    kept, cursor = [], 0
    for _, _, start, end in _location_blocks(body):
        kept.append(body[cursor:start])
        cursor = end
    kept.append(body[cursor:])
    return "".join(kept)


def test_every_proxied_location_owns_forwarded_headers():
    """proxy_set_header is not inherited once a location declares one."""
    blocks = _proxied_blocks()
    assert len(blocks) >= 7, [loc for loc, _ in blocks]
    for loc, body in blocks:
        for pattern in FORWARDED_HEADERS:
            assert re.search(pattern, body), (
                f"{loc} is missing `{pattern}`. proxy_set_header is NOT "
                "inherited into a location that sets any header of its own, so "
                "a client-supplied X-Forwarded-Prefix / X-Script-Name / "
                "X-Forwarded-Host / X-Auth-User would reach the upstream here."
            )


def test_prefix_header_is_nginx_owned():
    """The prefix Next turns into <base href> comes from nginx's own capture."""
    for loc, body in _proxied_blocks():
        if "/user/" in loc:
            assert "(?<studio_prefix>" in loc, (
                f"{loc} serves the KubeSpawner prefix but does not capture it"
            )
            assert re.search(
                r"proxy_set_header\s+X-Forwarded-Prefix\s+\$studio_prefix;", body
            ), f"{loc} must forward the captured prefix, not a client value"
        else:
            assert re.search(r'proxy_set_header\s+X-Forwarded-Prefix\s+"";', body), (
                f"{loc} is not under a prefix, so it must clear X-Forwarded-Prefix"
            )


def test_no_client_header_passes_through_by_omission():
    """A new proxying block that forgets the hygiene lines fails here."""
    blocks = _proxied_blocks()
    cleared = [loc for loc, body in blocks if re.search(r'X-Auth-User\s+"";', body)]
    assert len(cleared) == len(blocks), (
        "these proxying locations do not clear X-Auth-User: "
        f"{sorted({loc for loc, _ in blocks} - set(cleared))}"
    )


def test_security_headers_declared_at_server_level():
    server = _server_level(_directives())
    for header in (
        r'add_header\s+X-Content-Type-Options\s+"nosniff"\s+always;',
        r'add_header\s+Referrer-Policy\s+"strict-origin-when-cross-origin"\s+always;',
        r'add_header\s+X-Frame-Options\s+"SAMEORIGIN"\s+always;',
        r"add_header\s+Content-Security-Policy\s+",
    ):
        assert re.search(header, server), f"server level is missing {header}"

    csp = re.search(r'add_header\s+Content-Security-Policy\s+"([^"]*)"', server)
    assert csp, "CSP must be a quoted string"
    policy = csp.group(1)
    for directive in ("frame-ancestors 'self'", "base-uri 'self'", "object-src 'none'"):
        assert directive in policy, f"CSP is missing {directive}: {policy}"
    # Next.js ships inline scripts (theme bootstrap, hydration payload); adding
    # either of these without nonces would blank the app.
    assert "script-src" not in policy, policy
    assert "default-src" not in policy, policy


def test_location_add_header_only_in_jupyter_blocks():
    """add_header in a location drops the inherited server-level set."""
    overriding = [
        (loc, body)
        for loc, body, _, _ in _location_blocks(_directives())
        if "add_header" in body
    ]
    assert len(overriding) == 2, [loc for loc, _ in overriding]
    for loc, body in overriding:
        assert "jupyter" in loc, (
            f"{loc} declares add_header, which silently drops the server-level "
            "security headers for this location"
        )
        assert re.search(
            r'add_header\s+X-Content-Type-Options\s+"nosniff"\s+always;', body
        ), loc
        assert re.search(
            r'add_header\s+Referrer-Policy\s+"strict-origin-when-cross-origin"\s+always;',
            body,
        ), loc
        # Jupyter sets its own CSP; a second one is enforced as the
        # intersection and would break Lab's PDF/HTML viewers.
        assert "Content-Security-Policy" not in body, loc
        assert "X-Frame-Options" not in body, loc


def test_jupyter_blocks_forward_host_with_port():
    """jupyter_server compares Origin's host:port to Host, verbatim."""
    jupyter = [(loc, body) for loc, body in _proxied_blocks() if "jupyter" in loc]
    assert len(jupyter) == 2, [loc for loc, _ in jupyter]
    for loc, body in jupyter:
        assert re.search(r"proxy_set_header\s+Host\s+\$http_host;", body), (
            f"{loc} must forward Host with its port; $host strips it and "
            "jupyter_server then rejects every POST and kernel WebSocket on a "
            "non-default port as cross-origin"
        )
        assert not re.search(r"proxy_set_header\s+Host\s+\$host;", body), loc


def test_supervisord_services_bind_loopback():
    """nginx is the only listener that should be reachable from the pod net."""
    parser = configparser.RawConfigParser(strict=False)
    parser.read(SUPERVISORD)

    command = parser.get("program:studio-api", "command")
    assert "--host 127.0.0.1" in command, command
    assert "0.0.0.0" not in command, command

    environment = parser.get("program:studio-web", "environment")
    assert "127.0.0.1" in environment, environment
    assert "0.0.0.0" not in environment, environment


def test_nginx_upstreams_are_loopback():
    text = _directives()
    upstreams = re.findall(r"upstream\s+\S+\s*\{([^}]*)\}", text)
    assert len(upstreams) == 2, upstreams
    for body in upstreams:
        assert re.search(r"server\s+127\.0\.0\.1:", body), body
    assert "proxy_pass http://127.0.0.1:8889" in text


def test_chat_iframe_keeps_same_origin_sandbox():
    """The chat page origin-checks postMessage; that needs a same-origin frame."""
    assert "'allow-same-origin'" in _read(CHAT_EXT)


def test_base_path_validator_shape():
    """Tripwire for the <base href> validation the nginx headers back up."""
    urls = _read(URLS_TS)
    assert r"APP_PREFIX_FULL_RE = /^\/user\/[^/]+\/?$/" in urls
    assert "UNSAFE_BASE_PATH_CHARS" in urls
    # A protocol-relative base ("//evil.com/") repoints every URL in the app.
    assert 'value.includes("//")' in urls

    layout = _read(LAYOUT_TSX)
    assert 'normalizeBasePath(requestHeaders.get("x-forwarded-prefix"))' in layout
    assert "{ trusted: true }" in layout, (
        "NEXT_PUBLIC_BASE_PATH is operator-set and may name a prefix outside "
        "/user/<name>; the header must never be validated that leniently"
    )
    # Nothing in the stack sets it and nginx strips it, so it must not be read
    # as a base-path source (the comment above it may still mention it).
    assert 'requestHeaders.get("x-script-name")' not in layout
