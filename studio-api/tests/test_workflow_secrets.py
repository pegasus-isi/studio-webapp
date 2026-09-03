"""Credentials for workflow generators, settable from the GUI.

A generator gets a credential to its jobs by reading its own environment and
baking the value in with `add_env` — HTCondor gives jobs a clean environment,
so nothing else reaches them. In Studio that environment belongs to the API
server inside a pod the user cannot edit, so a workflow needing an API key was
unrunnable: `crophealth --data-source kaggle` read `os.environ.get('KAGGLE_KEY',
'')` and failed on a worker node with a Kaggle error that named nothing about
credentials.

Covered here: name/value validation (including the names that must be refused
because they change how the subprocess *runs*), write-only storage, and that
the overlay reaches a generator's environment.
"""

from __future__ import annotations

import os

import pytest

from services import workflow_secrets as ws


# --- names ----------------------------------------------------------------


def test_names_are_upcased():
    """`kaggle_key` and `KAGGLE_KEY` are the same credential.

    Storing both would show two rows for one thing and inject only one of them.
    """
    assert ws.normalize_name("kaggle_key") == "KAGGLE_KEY"
    assert ws.normalize_name("  Kaggle_Key  ") == "KAGGLE_KEY"


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "1KEY", "MY-KEY", "MY KEY", "MY.KEY", "MY$KEY", "KÉY"],
)
def test_unusable_names_are_refused(bad):
    with pytest.raises(ws.InvalidSecret):
        ws.normalize_name(bad)


@pytest.mark.parametrize(
    "reserved", ["PATH", "PYTHONPATH", "LD_PRELOAD", "HOME", "PEGASUS_PYTHON",
                 "path", "Ld_Preload"]
)
def test_names_that_change_how_things_run_are_refused(reserved):
    """These are not credentials, and a typo in one breaks every run.

    PATH or PYTHONPATH from this table would redirect which python and which
    libraries a generator gets; LD_PRELOAD executes chosen code; HOME moves the
    workspace. The failure would be attributed to anything but this setting.
    """
    with pytest.raises(ws.InvalidSecret) as e:
        ws.normalize_name(reserved)
    assert "log in to" in str(e.value)


def test_a_long_name_is_refused():
    with pytest.raises(ws.InvalidSecret):
        ws.normalize_name("A" * (ws.MAX_NAME_LEN + 1))


# --- values ---------------------------------------------------------------


def test_a_normal_token_is_accepted():
    assert ws.validate_value("abc123") == "abc123"
    # Whitespace inside a token is not ours to trim — some tokens contain it.
    assert ws.validate_value(" abc ") == " abc "


@pytest.mark.parametrize("bad", ["a\nb", "a\r\nb", "a\0b"])
def test_multiline_values_are_refused(bad):
    """A newline is truncated or reinterpreted by whatever reads the env.

    Refusing is better than storing something that will be silently mangled;
    the message points at the file-path alternative.
    """
    with pytest.raises(ws.InvalidSecret) as e:
        ws.validate_value(bad)
    assert "line breaks" in str(e.value)


def test_an_oversized_value_is_refused():
    with pytest.raises(ws.InvalidSecret):
        ws.validate_value("x" * (ws.MAX_VALUE_LEN + 1))


# --- endpoints ------------------------------------------------------------


def test_none_are_set_initially(client):
    body = client.get("/api/settings/workflow-secrets").json()
    assert body["secrets"] == []
    assert "encrypted_at_rest" in body


def test_setting_one_records_the_name_and_never_the_value(client):
    body = client.put(
        "/api/settings/workflow-secrets",
        json={"name": "kaggle_key", "value": "s3cret-token"},
    ).json()
    assert [s["name"] for s in body["secrets"]] == ["KAGGLE_KEY"]
    assert body["secrets"][0]["updated_at"]
    # The value must not come back from any read, in any field.
    assert "s3cret-token" not in str(body)
    assert "s3cret-token" not in str(client.get("/api/settings/workflow-secrets").json())


def test_setting_the_same_name_replaces_it(client):
    client.put("/api/settings/workflow-secrets",
               json={"name": "KAGGLE_KEY", "value": "one"})
    body = client.put("/api/settings/workflow-secrets",
                      json={"name": "KAGGLE_KEY", "value": "two"}).json()
    assert len(body["secrets"]) == 1


def test_a_reserved_name_is_refused_by_the_api(client):
    r = client.put("/api/settings/workflow-secrets",
                   json={"name": "PATH", "value": "/tmp"})
    assert r.status_code == 400
    assert "log in to" in r.json()["detail"]
    assert client.get("/api/settings/workflow-secrets").json()["secrets"] == []


def test_deleting_one(client):
    client.put("/api/settings/workflow-secrets",
               json={"name": "OPENAQ_TOKEN", "value": "abc"})
    body = client.delete("/api/settings/workflow-secrets/openaq_token").json()
    assert body["secrets"] == []


def test_deleting_one_that_is_not_there_is_a_404(client):
    assert client.delete("/api/settings/workflow-secrets/NOPE").status_code == 404


# --- the point of the feature: it reaches the generator -------------------


def test_the_overlay_carries_the_stored_secrets(client):
    client.put("/api/settings/workflow-secrets",
               json={"name": "KAGGLE_KEY", "value": "tok"})
    client.put("/api/settings/workflow-secrets",
               json={"name": "KAGGLE_USERNAME", "value": "me"})

    import asyncio
    env = asyncio.run(ws.subprocess_env())
    assert env["KAGGLE_KEY"] == "tok"
    assert env["KAGGLE_USERNAME"] == "me"
    # Overlaid, not replaced — a generator still needs PATH to run at all.
    assert env.get("PATH") == os.environ.get("PATH")


def test_the_overlay_refuses_a_reserved_name_from_the_database(client):
    """A hand-edited DB must not be able to inject PATH into a run.

    The API refuses these, so this can only come from someone writing the row
    directly — which is exactly when the check has to be at the read too.
    """
    import asyncio

    from main import db

    asyncio.run(db.set_workflow_secret("PATH", "/evil"))
    env = asyncio.run(ws.subprocess_env())
    assert env["PATH"] == os.environ.get("PATH")


def test_an_unreadable_database_does_not_break_a_run(client, monkeypatch):
    """A failed secrets read must degrade to "no secrets", not fail the run."""
    import asyncio

    from main import db

    async def boom():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "get_workflow_secret_env", boom)
    assert asyncio.run(ws.env_overlay()) == {}


# --- naming the credential a workflow wants -------------------------------


def _generator(tmp_path, body: str) -> str:
    path = tmp_path / "workflow_generator.py"
    path.write_text(body)
    return str(path)


def test_the_scan_finds_every_way_a_generator_reads_the_environment(tmp_path):
    from services.generator_introspect import generator_env_vars

    found = generator_env_vars(_generator(tmp_path, """
import os
a = os.environ["KAGGLE_KEY"]
b = os.environ.get('KAGGLE_USERNAME', '')
c = os.getenv("OPENAQ_API_KEY")
d = environ.get("NEON_TOKEN")
"""))
    assert found == ["KAGGLE_KEY", "KAGGLE_USERNAME", "NEON_TOKEN", "OPENAQ_API_KEY"]


def test_the_scan_ignores_variables_that_are_not_credentials(tmp_path):
    """Telling a scientist their analysis "needs" PATH buries the real name."""
    from services.generator_introspect import generator_env_vars

    found = generator_env_vars(_generator(tmp_path, """
import os
os.environ.get("PATH")
os.environ.get("HOME")
os.environ.get("PEGASUS_PYTHON")
os.environ.get("MY_TOKEN")
"""))
    assert found == ["MY_TOKEN"]


def test_the_scan_survives_a_generator_it_cannot_read(tmp_path):
    from services.generator_introspect import generator_env_vars

    assert generator_env_vars(str(tmp_path / "does-not-exist.py")) == []


def test_the_scan_does_not_import_the_generator(tmp_path):
    """It reads the source. Importing would run whatever is at module scope."""
    from services.generator_introspect import generator_env_vars

    marker = tmp_path / "imported"
    found = generator_env_vars(_generator(tmp_path, f"""
import os, pathlib
pathlib.Path({str(marker)!r}).write_text("ran")
key = os.environ.get("SOME_TOKEN")
"""))
    assert found == ["SOME_TOKEN"]
    assert not marker.exists()


def test_a_generator_run_by_the_flow_sees_the_secret(client, tmp_path):
    """The end the feature exists for, through the real call site.

    Everything above tests the pieces; this drives run_flow._run_generate with
    a generator that reports its own environment. If this passes and the UI
    lists the key, a workflow that needs an API key can run in Studio.
    """
    import asyncio

    from services import run_flow

    client.put("/api/settings/workflow-secrets",
               json={"name": "KAGGLE_KEY", "value": "the-real-token"})

    generator = tmp_path / "workflow_generator.py"
    generator.write_text(
        "import os, pathlib\n"
        "pathlib.Path('seen.txt').write_text(os.environ.get('KAGGLE_KEY', 'MISSING'))\n"
    )
    flow = run_flow.RunFlow(
        flow_id="f1", project_id="p1",
        stages=[run_flow.Stage(key="generate", label="Preparing your analysis")],
    )
    ok = asyncio.run(run_flow._run_generate(
        flow, project_dir=str(tmp_path),
        generator_file=str(generator), args=[],
    ))
    assert ok, flow.failure
    assert (tmp_path / "seen.txt").read_text() == "the-real-token"


def test_a_generator_still_gets_a_working_environment_with_no_secrets(client, tmp_path):
    """No secrets set must not mean an empty environment for the child."""
    import asyncio

    from services import run_flow

    generator = tmp_path / "workflow_generator.py"
    generator.write_text(
        "import os, pathlib, sys\n"
        "pathlib.Path('seen.txt').write_text(str(bool(os.environ.get('PATH'))))\n"
    )
    flow = run_flow.RunFlow(
        flow_id="f2", project_id="p2",
        stages=[run_flow.Stage(key="generate", label="Preparing your analysis")],
    )
    ok = asyncio.run(run_flow._run_generate(
        flow, project_dir=str(tmp_path),
        generator_file=str(generator), args=[],
    ))
    assert ok, flow.failure
    assert (tmp_path / "seen.txt").read_text() == "True"
