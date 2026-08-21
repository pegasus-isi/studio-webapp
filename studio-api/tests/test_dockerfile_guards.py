"""Guard the Dockerfile against unverified installs (PAS-SEC-09 / INF-4).

PR #66 replaced every unverified root-level install with a signed apt repo or
a sha256-pinned download. Three days later c50c700 put the Pegasus nightly
repo back with apt signature checking disabled, and nothing noticed: the image
built, CI was green, and CLAUDE.md kept claiming the installs were verified.
These assertions are what would have noticed. They read the Dockerfile as
text, so they run in the normal pytest gate — no Docker, no network.

"Verified" here means, per install shape:

- apt sources written by the Dockerfile carry ``signed-by=``, never
  ``trusted=yes`` or any other option that disables authentication;
- anything downloaded and then installed or executed (a .deb, get-pip.py, ...)
  is checked with ``sha256sum -c`` against a literal hash in the same RUN;
- nothing is piped from curl/wget straight into a shell or interpreter;
- the JupyterLab extension installs from a committed yarn.lock with
  ``--immutable``, and the frontend from package-lock.json with no fallback.

A repo that genuinely cannot satisfy one of these should change the assertion
in the same commit that changes the Dockerfile, so the reason lands in a diff
someone reviews — which is exactly what did not happen the first time.
"""

from __future__ import annotations

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCKERFILE = os.path.join(REPO_ROOT, "Dockerfile")
EXT_DIR = os.path.join(REPO_ROOT, "jupyter-pegasusai-chat")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _instructions(path: str = DOCKERFILE) -> list[str]:
    """Dockerfile instructions: comment lines dropped, continuations joined.

    Comments are dropped first so that prose describing a forbidden pattern
    (this file's own subject matter shows up in the Dockerfile comments) is not
    mistaken for the pattern itself. Joining `\\`-continuations means each
    returned element is one whole instruction, which is what lets the
    "download and check in the SAME RUN" assertions below mean anything.
    """
    lines = [ln for ln in _read(path).splitlines() if not ln.lstrip().startswith("#")]
    text = re.sub(r"\\\n", " ", "\n".join(lines))
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _code() -> str:
    return "\n".join(_instructions())


def _runs() -> list[str]:
    return [i for i in _instructions() if i.upper().startswith("RUN ")]


def test_apt_verification_is_never_disabled():
    code = _code()
    for bad in (
        r"trusted\s*=\s*yes",
        r"--allow-unauthenticated",
        r"--allow-insecure-repositories",
        r"AllowInsecureRepositories",
        r"AllowUnauthenticated",
    ):
        assert not re.search(bad, code, re.I), (
            f"Dockerfile disables apt verification: {bad}"
        )


def test_every_apt_source_written_here_is_signed_by():
    """An apt source line the image writes must name the keyring that signs it."""
    for m in re.finditer(r"\bdeb(?:-src)?\s+\[([^\]]*)\]", _code()):
        assert "signed-by=" in m.group(1), f"apt source without signed-by: {m.group(0)}"


def test_nothing_is_piped_from_the_network_into_an_interpreter():
    pat = re.compile(
        r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(bash|sh|zsh|dash|python3?|perl|node)\b"
    )
    for run in _runs():
        assert not pat.search(run), f"network pipe into interpreter: {run[:160]}"


# An artifact is something that gets installed or executed as root. `.asc` and
# `.list` (the HTCondor keyring and source list) are deliberately not here:
# they are inert until apt uses them, and apt's own signature check covers them.
_ARTIFACT = r"\.(deb|rpm|py|sh|tar\.gz|tgz|tar\.xz|whl|sif|run|bin)\b"
# `[^;&|]*?` cannot cross a `&&`/`;`/`|`, so these only match within one
# command of a RUN — a `-o foo.deb` in a different command does not bleed in.
_FETCH = re.compile(r"\b(curl|wget)\b[^;&|]*?(?:\s-o|\s-O|--output)\s+\S+" + _ARTIFACT)
_CONSUME = re.compile(
    r"apt-get\s+install\b[^;&|]*\s\S*\.deb\b"
    r"|\bdpkg\s+-i\b"
    r"|\b(python3?|sh|bash)\s+/tmp/\S+"
)


def test_downloaded_artifacts_are_sha256_checked():
    """Whatever a RUN fetches or installs from a file, it verifies first."""
    for run in _runs():
        if _FETCH.search(run) or _CONSUME.search(run):
            assert "sha256sum -c" in run, f"unverified download/install: {run[:200]}"


def test_pinned_hashes_are_literal_sha256():
    """The hash lives in the file under review, not in a variable from elsewhere."""
    code = _code()
    for name in ("PEGASUS_DEB_SHA256", "APPTAINER_DEB_SHA256"):
        assert re.search(rf'^ARG\s+{name}="?[0-9a-f]{{64}}"?\s*$', code, re.M), (
            f"ARG {name} must be a 64-hex sha256 literal"
        )


def test_extension_installs_from_a_committed_immutable_lockfile():
    lock = os.path.join(EXT_DIR, "yarn.lock")
    assert os.path.isfile(lock) and os.path.getsize(lock) > 1000, (
        "jupyter-pegasusai-chat/yarn.lock is missing or truncated — regenerate it "
        "(README: 'JupyterLab extension lockfile')"
    )
    assert "__metadata:" in _read(lock)[:500], "yarn.lock is not a Yarn Berry lockfile"
    code = _code()
    assert "touch yarn.lock" not in code, (
        "`touch yarn.lock` throws the committed lockfile away and re-resolves every build"
    )
    installs = [r for r in _runs() if "jlpm install" in r]
    assert installs, (
        "no `jlpm install` in the Dockerfile — did the extension build move?"
    )
    assert all("jlpm install --immutable" in r for r in installs), (
        "jlpm install must be --immutable: Yarn Berry only defaults it on when CI is set"
    )


def test_extension_lockfile_is_not_ignored():
    """A lockfile that git or the build context drops is a lockfile that is not used."""
    for ignore in (
        os.path.join(EXT_DIR, ".gitignore"),
        os.path.join(REPO_ROOT, ".gitignore"),
        os.path.join(REPO_ROOT, ".dockerignore"),
    ):
        if not os.path.exists(ignore):
            continue
        entries = {
            ln.strip().lstrip("/")
            for ln in _read(ignore).splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        }
        assert not entries & {"yarn.lock", "**/yarn.lock"}, (
            f"{ignore} ignores yarn.lock"
        )


def test_frontend_install_has_no_floating_fallback():
    code = _code()
    assert "|| npm install" not in code, (
        "the `npm ci || npm install` fallback floats every frontend dependency "
        "whenever npm ci fails for any reason"
    )
    assert re.search(r"^RUN npm ci\b", code, re.M), "frontend must install with npm ci"
    assert "package-lock.json*" not in code, (
        "glob COPY would tolerate a missing lockfile"
    )
