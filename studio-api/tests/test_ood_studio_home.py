"""An Open OnDemand session gives Studio a home of its own (#482).

Everything Studio writes is $HOME-relative, and on a cluster $HOME is the
user's real home, where their own codex/opencode/claude/Pegasus configuration
already lives. The OOD app therefore runs the image with
`apptainer --home "$STUDIO_HOME"`; `ood-app/template/studio-home.sh` decides
and prepares that directory, and the entrypoint backs it up for a launch that
only set the variable.

These run the real shell, not a reimplementation: the rules that matter (never
the real home, never an ancestor of it, never clobber what the user put there)
live in that shell and nowhere else.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ENTRYPOINT = os.path.join(_REPO, "docker", "entrypoint.sh")
_TEMPLATES = os.path.join(_REPO, "docker", "nginx")
_OOD = os.path.join(_REPO, "ood-app")
_HELPER = os.path.join(_OOD, "template", "studio-home.sh")

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is required to run the scripts"
)


def _helper(fn: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n. "$0"\n{fn} "$@"', _HELPER, *args],
        capture_output=True,
        text=True,
    )


# --- studio_home_resolve ------------------------------------------------------


def test_the_site_default_is_used_when_the_form_is_left_empty(tmp_path):
    real = tmp_path / "u"
    real.mkdir()
    proc = _helper("studio_home_resolve", "", f"{real}/pegasus-studio", str(real))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"{real}/pegasus-studio"


def test_a_tilde_path_from_the_form_expands_against_the_real_home(tmp_path):
    real = tmp_path / "u"
    real.mkdir()
    proc = _helper("studio_home_resolve", "~/studio/", "/unused", str(real))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"{real}/studio"


@pytest.mark.parametrize("requested", ["studio", "./studio", "~user/x"])
def test_a_relative_path_is_refused(tmp_path, requested):
    proc = _helper("studio_home_resolve", requested, "/unused", str(tmp_path))
    assert proc.returncode != 0
    assert "absolute" in proc.stderr


@pytest.mark.parametrize("suffix", ["", "/", "//", "~"])
def test_the_real_home_itself_is_refused(tmp_path, suffix):
    """The one directory this whole mechanism exists to stay out of."""
    real = tmp_path / "u"
    real.mkdir()
    requested = "~" if suffix == "~" else f"{real}{suffix}"
    proc = _helper("studio_home_resolve", requested, "/unused", str(real))
    assert proc.returncode != 0
    assert "home directory itself" in proc.stderr


def test_a_symlink_to_the_real_home_is_refused(tmp_path):
    real = tmp_path / "u"
    real.mkdir()
    (tmp_path / "alias").symlink_to(real)
    proc = _helper("studio_home_resolve", str(tmp_path / "alias"), "", str(real))
    assert proc.returncode != 0


@pytest.mark.parametrize("ancestor", ["/", "PARENT"])
def test_an_ancestor_of_the_real_home_is_refused(tmp_path, ancestor):
    real = tmp_path / "u"
    real.mkdir()
    requested = str(tmp_path) if ancestor == "PARENT" else ancestor
    proc = _helper("studio_home_resolve", requested, "", str(real))
    assert proc.returncode != 0


@pytest.mark.parametrize("suffix", ["/new/..", "/new/../", "/.", "/a/./b"])
def test_dot_components_are_refused(tmp_path, suffix):
    """`~/new/..` passes a string comparison while `new` does not exist, then
    names the real home once `mkdir -p` has created it."""
    real = tmp_path / "u"
    real.mkdir()
    proc = _helper("studio_home_resolve", f"{real}{suffix}", "", str(real))
    assert proc.returncode != 0
    assert "'.' or '..'" in proc.stderr


def test_symlinks_are_resolved_even_above_a_not_yet_created_leaf(tmp_path):
    """With ~/up -> the home's parent, ~/up/u is the home and ~/up contains it,
    whatever the spelling. A path below the home through the same link is
    still fine, including one whose leaf does not exist yet."""
    real = tmp_path / "u"
    real.mkdir()
    (real / "up").symlink_to(tmp_path)
    assert _helper("studio_home_resolve", f"{real}/up/u", "", str(real)).returncode != 0
    assert _helper("studio_home_resolve", f"{real}/up", "", str(real)).returncode != 0
    inside = _helper("studio_home_resolve", f"{real}/up/u/new/studio", "", str(real))
    assert inside.returncode == 0, inside.stderr


def test_a_sibling_sharing_a_name_prefix_is_not_mistaken_for_an_ancestor(tmp_path):
    """/home/u2 is not inside /home/u, and /home/u-studio does not contain it."""
    real = tmp_path / "u"
    real.mkdir()
    proc = _helper("studio_home_resolve", f"{real}-studio", "", str(real))
    assert proc.returncode == 0, proc.stderr


# --- studio_home_prepare ------------------------------------------------------


def test_prepare_creates_a_private_directory_and_links_credentials(tmp_path):
    real = tmp_path / "u"
    (real / ".condor" / "tokens.d").mkdir(parents=True)
    (real / ".gitconfig").write_text("[user]\n", encoding="utf-8")
    home = real / "pegasus-studio"

    proc = _helper(
        "studio_home_prepare", str(home), str(real), ".condor/tokens.d .gitconfig .ssh"
    )
    assert proc.returncode == 0, proc.stderr
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert os.readlink(home / ".condor" / "tokens.d") == str(real / ".condor/tokens.d")
    assert os.readlink(home / ".gitconfig") == str(real / ".gitconfig")
    # Absent in the real home: nothing to link, and no dangling link made.
    assert not os.path.lexists(home / ".ssh")


def test_prepare_never_replaces_what_is_already_in_the_studio_home(tmp_path):
    real = tmp_path / "u"
    real.mkdir()
    (real / ".gitconfig").write_text("real\n", encoding="utf-8")
    (real / ".ssh").mkdir()
    home = tmp_path / "studio"
    home.mkdir()
    (home / ".gitconfig").write_text("mine\n", encoding="utf-8")
    (home / ".ssh").symlink_to(tmp_path / "gone")  # dangling, still the user's

    proc = _helper("studio_home_prepare", str(home), str(real), ".gitconfig .ssh")
    assert proc.returncode == 0, proc.stderr
    assert (home / ".gitconfig").read_text(encoding="utf-8") == "mine\n"
    assert os.readlink(home / ".ssh") == str(tmp_path / "gone")


def test_prepare_refuses_entries_that_climb_out_of_the_home(tmp_path):
    real = tmp_path / "u"
    real.mkdir()
    (tmp_path / "secret").write_text("x", encoding="utf-8")
    home = tmp_path / "studio"
    proc = _helper("studio_home_prepare", str(home), str(real), "../secret")
    assert proc.returncode == 0
    assert list(home.iterdir()) == []


def test_an_empty_inherit_list_links_nothing(tmp_path):
    real = tmp_path / "u"
    (real / ".condor" / "tokens.d").mkdir(parents=True)
    home = tmp_path / "studio"
    proc = _helper("studio_home_prepare", str(home), str(real), "")
    assert proc.returncode == 0, proc.stderr
    assert list(home.iterdir()) == []


# --- entrypoint fallback ------------------------------------------------------


def _apply_studio_home(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script = fh.read()
    match = re.search(
        r"^apply_studio_home\(\) \{\n.*?^\}$", script, re.MULTILINE | re.DOTALL
    )
    assert match, "apply_studio_home() not found in docker/entrypoint.sh"
    base = {k: v for k, v in os.environ.items() if not k.startswith("STUDIO_")}
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{match.group(0)}\napply_studio_home\n'
         'bash -c \'printf "%s" "$HOME"\''],
        capture_output=True,
        text=True,
        env={**base, **env},
    )


def test_entrypoint_moves_home_when_only_studio_home_was_set():
    proc = _apply_studio_home({"HOME": "/home/u", "STUDIO_HOME": "/home/u/studio/"})
    assert proc.returncode == 0, proc.stderr
    # Exported, so supervisord and every tool it starts inherit it.
    assert proc.stdout == "/home/u/studio"
    assert "passwd" in proc.stderr


def test_entrypoint_is_quiet_when_apptainer_already_moved_home():
    proc = _apply_studio_home({"HOME": "/home/u/studio", "STUDIO_HOME": "/home/u/studio"})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "/home/u/studio"
    assert proc.stderr == ""


def test_entrypoint_leaves_home_alone_without_studio_home():
    """KubeSpawner and the plain container: the home is already Studio's."""
    proc = _apply_studio_home({"HOME": "/home/jovyan"})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "/home/jovyan"


def test_entrypoint_refuses_a_relative_studio_home():
    proc = _apply_studio_home({"HOME": "/home/u", "STUDIO_HOME": "studio"})
    assert proc.returncode != 0
    assert "absolute" in proc.stderr


def _render_only(tmp_path, **env: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "entrypoint.sh"
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script.write_text(
            fh.read().replace("/opt/pegasus-ai/templates", _TEMPLATES),
            encoding="utf-8",
        )
    base = {k: v for k, v in os.environ.items() if not k.startswith("STUDIO_")}
    return subprocess.run(
        ["bash", str(script), "--render-only"],
        capture_output=True,
        text=True,
        env={
            **base,
            "HOME": str(tmp_path / "home"),
            "STUDIO_RUNTIME_DIR": str(tmp_path / "rt"),
            **env,
        },
    )


def test_render_only_still_touches_no_home(tmp_path):
    """The image build runs --render-only; it must leave no state behind."""
    proc = _render_only(tmp_path, STUDIO_HOME=str(tmp_path / "studio"))
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "studio").exists()
    assert not (tmp_path / "home").exists()


def test_a_fqdn_session_host_in_the_url_prefix_is_accepted(tmp_path):
    """OOD's prefix is /node/<host>/<port>, and <host> is a FQDN on many
    clusters. Refusing the dot killed the session before the proxy started."""
    prefix = "/node/c001.cluster.example.edu/45678"
    proc = _render_only(
        tmp_path, STUDIO_AUTH_TOKEN="tok_en-123", STUDIO_URL_PREFIX=prefix
    )
    assert proc.returncode == 0, proc.stderr
    rendered = (tmp_path / "rt" / "prefix-proxy.conf").read_text(encoding="utf-8")
    assert f"location {prefix}/api/" in rendered


# --- the OOD app itself -------------------------------------------------------


def test_the_form_offers_the_studio_directory():
    with open(os.path.join(_OOD, "form.yml.erb"), encoding="utf-8") as fh:
        form = fh.read()
    assert re.search(r"^  studio_home:$", form, re.MULTILINE)
    assert re.search(r"^  - studio_home$", form, re.MULTILINE)


@pytest.mark.skipif(shutil.which("ruby") is None, reason="ruby renders the ERB")
@pytest.mark.parametrize("studio_home", ["", "~/my studio", "/scratch/u/it's"])
def test_the_job_script_renders_to_valid_bash(tmp_path, studio_home):
    """Render script.sh.erb the way the dashboard does and syntax-check it.
    The form value is free text, so it must arrive quoted whatever it holds."""
    out = tmp_path / "script.sh"
    proc = subprocess.run(
        [
            "ruby", "-rerb", "-e",
            "studio_home = ARGV[1]; "
            "File.write(ARGV[2], ERB.new(File.read(ARGV[0]), trim_mode: '-')"
            ".result(binding))",
            os.path.join(_OOD, "template", "script.sh.erb"),
            studio_home,
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    script = out.read_text(encoding="utf-8")
    check = subprocess.run(["bash", "-n", str(out)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    assert '--home "${STUDIO_HOME}"' in script
    assert "--cleanenv" in script
