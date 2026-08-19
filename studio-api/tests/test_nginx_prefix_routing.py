"""Guard the KubeSpawner prefix routing in docker/nginx/default.conf.

Every browser API call 404'd behind `/user/<name>/` for two months. The cause
was `rewrite ... break` paired with a URI-less `proxy_pass`: nginx sent the
upstream the *original* URI truncated to the rewritten URI's length, so
`/user/kthare10/api/health` arrived as `/user/kthar` — right length, wrong bytes.
It survived testing because a direct port has no prefix to strip, and the app's
root path happened to work (its rewritten URI is "/", one character).

Nothing in the Python suite can exercise nginx, so this asserts the shape of the
config instead: prefix-stripping is done by a capture plus `proxy_pass`, never by
`rewrite`.
"""

from __future__ import annotations

import os
import re

CONF = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docker", "nginx", "default.conf",
)


def _blocks(path: str | None = None) -> list[tuple[str, str]]:
    """(location line, body) for each location block matching `/user/<name>`."""
    text = open(path or CONF).read()
    out = []
    for m in re.finditer(r"^[ \t]*location[^\n]*\{", text, re.M):
        header = m.group(0).strip()
        if "/user/" not in header:
            continue
        depth, i = 1, m.end()
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        out.append((header, text[m.end():i]))
    return out


def test_prefix_blocks_exist():
    names = [loc for loc, _ in _blocks()]
    assert any("/api" in n for n in names), names
    assert any("/ws" in n for n in names), names


def test_no_rewrite_in_prefix_blocks():
    """`rewrite ... break` + URI-less proxy_pass truncates the upstream path."""
    for loc, body in _blocks():
        assert "rewrite" not in body, (
            f"{loc} uses rewrite; strip the prefix with a named capture and "
            "proxy_pass http://upstream$capture$is_args$args instead"
        )


def test_proxied_blocks_pass_a_captured_path():
    """A URI-less proxy_pass in these blocks is the bug; require a variable."""
    for loc, body in _blocks():
        m = re.search(r"proxy_pass\s+(http://[^;\s]+);", body)
        if not m:
            continue  # e.g. the 308 redirect block
        target = m.group(1)
        if "jupyter" in loc:
            continue  # JupyterLab is served AT the prefix; nothing to strip
        assert "$" in target, (
            f"{loc} has proxy_pass {target} with no captured path — nginx will "
            "send the original URI truncated to the rewritten length"
        )
        assert "$is_args$args" in body, f"{loc} drops the query string"
