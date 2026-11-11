"""The image's Pegasus system properties reach ~/.pegasusrc (#668).

/etc/pegasus/pegasus.properties is never read in Studio: the packaged Pegasus
lives in a venv whose sysconfdir is elsewhere, and planning usually runs on the
submit host. The entrypoint copies each property into ~/.pegasusrc, which
every plan reads, without overriding a key the user set there.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ENTRYPOINT = os.path.join(_REPO, "docker", "entrypoint.sh")
_IMAGE_PROPS = os.path.join(_REPO, "docker", "pegasus", "pegasus.properties")


def _block() -> str:
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script = fh.read()
    m = re.search(
        r"^# ---- Pegasus system properties \(#668\) ----\n(.*?)^# ---- Private state ----\n",
        script,
        re.M | re.S,
    )
    assert m, "Pegasus system properties block not found in docker/entrypoint.sh"
    return m.group(1)


def _run(tmp_path, props: str, rc: str | None):
    sysprops = tmp_path / "pegasus.properties"
    sysprops.write_text(props)
    pegasusrc = tmp_path / ".pegasusrc"
    if rc is not None:
        pegasusrc.write_text(rc)
    proc = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + _block()],
        env={
            **os.environ,
            "PEGASUSRC": str(pegasusrc),
            "PEGASUS_SYSTEM_PROPS": str(sysprops),
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return pegasusrc, proc.stdout


def test_the_image_ships_the_access_properties():
    with open(_IMAGE_PROPS, encoding="utf-8") as fh:
        text = fh.read()
    assert "pegasus.monitord.encoding = json" in text
    assert "pegasus.catalog.workflow.amqp.url" in text


def test_properties_are_added_to_an_existing_pegasusrc(tmp_path):
    rc, out = _run(
        tmp_path,
        "pegasus.monitord.encoding = json\n"
        "pegasus.catalog.workflow.amqp.url = amqp://h:5672/prod/workflows\n",
        "pegasus.catalog.site.repo.file = pegasus-studio.yml\n",
    )
    lines = rc.read_text().splitlines()
    assert lines == [
        "pegasus.catalog.site.repo.file = pegasus-studio.yml",
        "pegasus.monitord.encoding = json",
        "pegasus.catalog.workflow.amqp.url = amqp://h:5672/prod/workflows",
    ]
    assert stat.S_IMODE(os.stat(rc).st_mode) == 0o600
    assert "Added 2 Pegasus system properties" in out


def test_a_missing_pegasusrc_is_created(tmp_path):
    rc, _ = _run(tmp_path, "pegasus.monitord.encoding = json\n", None)
    assert rc.read_text() == "pegasus.monitord.encoding = json\n"


def test_a_key_the_user_set_is_left_alone(tmp_path):
    # Including an empty value, which is how a user turns the AMQP sink off.
    rc, out = _run(
        tmp_path,
        "pegasus.monitord.encoding = json\n"
        "pegasus.catalog.workflow.amqp.url = amqp://h/prod/workflows\n",
        "pegasus.monitord.encoding=bp\n  pegasus.catalog.workflow.amqp.url =\n",
    )
    assert rc.read_text() == (
        "pegasus.monitord.encoding=bp\n  pegasus.catalog.workflow.amqp.url =\n"
    )
    assert out == ""


def test_running_twice_adds_nothing_the_second_time(tmp_path):
    props = "pegasus.monitord.encoding = json\n"
    rc, _ = _run(tmp_path, props, "")
    first = rc.read_text()
    rc, out = _run(tmp_path, props, first)
    assert rc.read_text() == first
    assert out == ""


def test_dots_in_a_key_are_not_wildcards(tmp_path):
    # A regex match on "pegasus.monitord.encoding" would also accept this
    # unrelated key and wrongly skip the real one.
    rc, _ = _run(
        tmp_path,
        "pegasus.monitord.encoding = json\n",
        "pegasusXmonitordXencoding = other\n",
    )
    assert "pegasus.monitord.encoding = json" in rc.read_text()


def test_a_longer_key_with_the_same_prefix_does_not_count(tmp_path):
    rc, _ = _run(
        tmp_path,
        "pegasus.monitord.encoding = json\n",
        "pegasus.monitord.encoding.extra = x\n",
    )
    assert "pegasus.monitord.encoding = json" in rc.read_text()


def test_comments_blanks_and_a_last_line_without_newline(tmp_path):
    rc, _ = _run(
        tmp_path,
        "# a comment\n\n! another\npegasus.monitord.encoding = json",
        "",
    )
    assert rc.read_text() == "pegasus.monitord.encoding = json\n"


def test_no_system_file_is_nothing_to_do(tmp_path):
    pegasusrc = tmp_path / ".pegasusrc"
    proc = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + _block()],
        env={
            **os.environ,
            "PEGASUSRC": str(pegasusrc),
            "PEGASUS_SYSTEM_PROPS": str(tmp_path / "absent"),
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert not pegasusrc.exists()
