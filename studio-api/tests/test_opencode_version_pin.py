"""The OpenCode server and its SDK must be the same version.

`@opencode-ai/sdk` is generated from the server's OpenAPI document, and the
chat panel is typed against it (#367). If the two drift, `tsc` still passes —
the types describe a server that isn't running — and the mismatch shows up as a
payload that renders wrong or an endpoint that 404s at runtime, which is
exactly the class of bug the "proxy, don't adapt" design is meant to make
impossible.

Nothing in the Python suite can exercise either package, so this asserts the
declared versions agree. It is here rather than in a CI-only shell step so it
fails on a developer's machine too, before the PR.
"""

from __future__ import annotations

import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCKERFILE = os.path.join(ROOT, "Dockerfile")
PACKAGE_JSON = os.path.join(ROOT, "studio-web", "package.json")


def _server_version() -> str:
    text = open(DOCKERFILE).read()
    match = re.search(r"npm install -g opencode-ai@([0-9][^\s\\]*)", text)
    assert match, "Dockerfile no longer pins opencode-ai to an exact version"
    return match.group(1)


def _sdk_version() -> str:
    package = json.load(open(PACKAGE_JSON))
    version = package["dependencies"].get("@opencode-ai/sdk")
    assert version, "studio-web dropped its @opencode-ai/sdk dependency"
    return version


def test_sdk_matches_the_server_in_the_image():
    server, sdk = _server_version(), _sdk_version()
    assert server == sdk, (
        f"opencode-ai@{server} in the Dockerfile but @opencode-ai/sdk@{sdk} in "
        "studio-web/package.json — bump both together"
    )


def test_sdk_version_is_exact():
    """A range would let a build resolve a different SDK than the server."""
    sdk = _sdk_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+", sdk), (
        f"@opencode-ai/sdk must be pinned exactly, not {sdk!r}"
    )
