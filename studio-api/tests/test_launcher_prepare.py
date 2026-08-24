"""Exercise launcher/studio-launch --prepare-only (the $HOME provisioning).

Focus: the two phone-home defaults must be opt-in. The AMQP telemetry used to
be baked at /etc/pegasus/pegasus.properties and pegasus.agent.url was written
unconditionally — a lab running the image or the native dist could neither
see nor disable either. Now the launcher writes both per-user and only when
STUDIO_TELEMETRY=on / STUDIO_PEGASUS_AGENT_URL is set (the image ENVs both to
keep hosted behavior; the native dist sets nothing).

Runs the real script with a stub STUDIO_API_DIR (init_db.py/propagate_llm.py
stand-ins), so no DB, Docker, or network is involved.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCH = REPO_ROOT / "launcher" / "studio-launch"


def _prepare(tmp_path: Path, env_overrides: dict[str, str] | None = None) -> Path:
    """Run --prepare-only against a scratch HOME; returns the HOME path."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    api_dir = tmp_path / "api"
    api_dir.mkdir(exist_ok=True)
    # Stubs for the two scripts prepare invokes; recording files prove they ran.
    (api_dir / "init_db.py").write_text("open('init_db.ran', 'w').close()\n")
    (api_dir / "propagate_llm.py").write_text("pass\n")

    env = os.environ.copy()
    for key in [k for k in env if k.startswith("STUDIO_")]:
        del env[key]
    env["HOME"] = str(home)
    env["STUDIO_RUNTIME_DIR"] = str(tmp_path / "runtime")
    env["STUDIO_API_DIR"] = str(api_dir)
    env["STUDIO_KNOWLEDGE_SEED"] = str(tmp_path / "no-seed")
    env["STUDIO_OVERLAY"] = str(tmp_path / "no-overlay.md")
    env.update(env_overrides or {})
    proc = subprocess.run(
        ["bash", str(LAUNCH), "--prepare-only"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return home


def test_prepare_provisions_home(tmp_path: Path):
    home = _prepare(tmp_path)
    assert (home / ".studio" / ".initialized").exists()
    assert (home / "work" / "workflows").is_dir()
    assert (tmp_path / "api" / "init_db.ran").exists()


def test_secret_key_file_generated_private(tmp_path: Path):
    """No master secret set (a lab won't manage one): the launcher generates
    the S7 keys-at-rest file itself, 0600, and reuses it on restart."""
    home = _prepare(tmp_path)
    keyfile = home / ".studio" / "secret.key"
    assert keyfile.exists()
    assert (keyfile.stat().st_mode & 0o777) == 0o600
    first = keyfile.read_text()
    assert len(first.strip()) >= 48
    _prepare(tmp_path)
    assert keyfile.read_text() == first, "key must survive restarts"


def test_no_phone_home_by_default(tmp_path: Path):
    """Native-dist default: neither the agent URL nor the AMQP telemetry is
    written unless the packaging or site opted in."""
    home = _prepare(tmp_path)
    pegasus_dir = home / ".pegasus"
    conf = pegasus_dir / "pegasus.conf"
    props = pegasus_dir / "pegasus.properties"
    assert not conf.exists() or "pegasus.agent.url" not in conf.read_text()
    assert not props.exists() or "amqp" not in props.read_text()


def test_agent_url_written_when_configured(tmp_path: Path):
    home = _prepare(
        tmp_path, {"STUDIO_PEGASUS_AGENT_URL": "https://agent.example.org"}
    )
    conf = (home / ".pegasus" / "pegasus.conf").read_text()
    assert "pegasus.agent.url = https://agent.example.org" in conf
    # Idempotent across restarts, and user edits win over the env value.
    _prepare(tmp_path, {"STUDIO_PEGASUS_AGENT_URL": "https://other.example.org"})
    conf = (home / ".pegasus" / "pegasus.conf").read_text()
    assert conf.count("pegasus.agent.url") == 1
    assert "agent.example.org" in conf


def test_telemetry_written_only_when_on(tmp_path: Path):
    home = _prepare(tmp_path, {"STUDIO_TELEMETRY": "on"})
    props = (home / ".pegasus" / "pegasus.properties").read_text()
    assert "pegasus.monitord.encoding = json" in props
    assert (
        "pegasus.catalog.workflow.amqp.url = "
        "amqp://friend:donatedata@msgs.pegasus.isi.edu:5672/prod/workflows"
    ) in props
    # Idempotent: a second start must not duplicate the keys.
    _prepare(tmp_path, {"STUDIO_TELEMETRY": "on"})
    props = (home / ".pegasus" / "pegasus.properties").read_text()
    assert props.count("amqp.url") == 1


def test_telemetry_endpoint_override(tmp_path: Path):
    home = _prepare(
        tmp_path,
        {
            "STUDIO_TELEMETRY": "on",
            "STUDIO_TELEMETRY_AMQP_URL": "amqp://lab@mq.example.org:5672/x",
        },
    )
    props = (home / ".pegasus" / "pegasus.properties").read_text()
    assert "amqp://lab@mq.example.org:5672/x" in props
    assert "msgs.pegasus.isi.edu" not in props


def test_user_properties_edits_win(tmp_path: Path):
    """A user who pointed telemetry elsewhere (or neutered it) keeps their
    setting — append-if-absent, never overwrite."""
    home = tmp_path / "home"
    pegasus_dir = home / ".pegasus"
    pegasus_dir.mkdir(parents=True)
    (pegasus_dir / "pegasus.properties").write_text(
        "pegasus.catalog.workflow.amqp.url = amqp://mine.example.org/q\n"
    )
    _prepare(tmp_path, {"STUDIO_TELEMETRY": "on"})
    props = (pegasus_dir / "pegasus.properties").read_text()
    assert props.count("amqp.url") == 1
    assert "mine.example.org" in props
