"""Tests for the container image builder service and endpoints."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import services.image_builder as ib


# --- pure helpers ---


def test_normalize_registry_variants():
    assert ib.normalize_registry("docker:///kthare10/") == "docker.io/kthare10"
    assert ib.normalize_registry("kthare10") == "docker.io/kthare10"
    assert ib.normalize_registry("ghcr.io/pegasus-isi") == "ghcr.io/pegasus-isi"
    assert ib.normalize_registry("localhost:5000/dev") == "localhost:5000/dev"
    assert ib.normalize_registry("") == ""
    assert ib.normalize_registry("docker://docker.io/foo/") == "docker.io/foo"


def test_image_ref():
    assert (
        ib.image_ref("docker:///kthare10/", "Mag-Workflow", "latest")
        == "docker.io/kthare10/mag-workflow:latest"
    )
    assert ib.image_ref("", "soil", "v2") == "soil:v2"
    assert ib.image_ref("kthare10", "x", "") == "docker.io/kthare10/x:latest"


def test_build_command_ignores_the_fakeroot_command_and_needs_a_range_for_fakeroot(
    monkeypatch,
):
    """These two flags look like a pair and do opposite things here.

    Measured on real definitions in the same container:

        --fakeroot --ignore-fakeroot-command -> 37,461 bytes, empty filesystem,
                                                "Build complete", exit 0
        (no flags)                           -> FATAL in %post, libfakeroot.so
                                                needs GLIBC_2.38
        --ignore-fakeroot-command            -> 312 MB, inspects clean

    Dropping both (the first attempt at fixing the empty-image bug) broke every
    build whose base image is older than the host, which is the ordinary case:
    ubuntu:22.04 carries glibc 2.35 and this image is Debian trixie (2.41).

    The empty-image result was measured on a host with **no subuid range for
    the running user**, which is why it happened: --fakeroot then has no
    backend left, since --ignore-fakeroot-command refuses the libfakeroot one
    and this image is not a setuid install. So the rule is not "never
    --fakeroot" but "not without a range" — and whether a range exists is
    deployment-specific (a VM running as `studio` has one; a KubeSpawner pod
    running as a per-user LDAP uid does not).
    """
    monkeypatch.setattr(ib, "subuid_range", lambda: None)
    cmd = ib.build_command("apptainer", "img.sif", "img.def")
    assert cmd[:4] == ["apptainer", "build", "--force", "--ignore-fakeroot-command"]
    assert cmd[-2:] == ["img.sif", "img.def"]
    assert "--fakeroot" not in cmd

    # With a range, fakeroot has the one backend that works: many ids mapped
    # through setuid newuidmap, so chown/chgrp succeed and no library is
    # preloaded. --ignore-fakeroot-command still refuses the broken backend.
    monkeypatch.setattr(
        ib, "subuid_range", lambda: ("studio:100000:65536", "studio:100000:65536")
    )
    cmd = ib.build_command("apptainer", "img.sif", "img.def")
    assert "--fakeroot" in cmd
    assert "--ignore-fakeroot-command" in cmd


def test_a_range_needs_both_subuid_and_subgid(tmp_path, monkeypatch):
    """uids alone still leaves one gid mapped, so chgrp keeps failing."""
    import pwd

    name = pwd.getpwuid(os.geteuid()).pw_name
    (tmp_path / "subuid").write_text(f"{name}:100000:65536\n")
    (tmp_path / "subgid").write_text("someone-else:100000:65536\n")

    real_open = open

    def fake_open(path, *a, **k):
        if path == "/etc/subuid":
            return real_open(tmp_path / "subuid", *a, **k)
        if path == "/etc/subgid":
            return real_open(tmp_path / "subgid", *a, **k)
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    assert ib.subuid_range() is None


def test_an_ownership_failure_is_explained_rather_than_left_as_a_dpkg_code():
    """The visible error is a dpkg exit code; the cause is three lines earlier.

    Reproduced on two real workflows: a package postinst ran chgrp, only one
    gid is mapped into an unprivileged build, and the kernel returned EINVAL.
    Nothing in the output says so, and the file it names exists on both
    machines — so the next step someone takes is inspecting a definition that
    is fine.
    """
    out = [
        "Setting up openssh-client (1:8.4p1-5+deb11u7) ...",
        "chgrp: changing group of '/usr/bin/ssh-agent': Invalid argument",
        "dpkg: error processing package openssh-client (--configure):",
        "FATAL:   While performing build: while running %post section: exit status 100",
    ]
    hint = "\n".join(ib.ownership_failure_hint(out))
    assert "could not change file ownership" in hint
    # Names the culprit, so nobody has to find it in 900 lines of apt output.
    assert "openssh-client" in hint
    # And says plainly where the fault is not.
    assert "definition file is not at fault" in hint


def test_no_ownership_hint_for_an_unrelated_failure():
    assert ib.ownership_failure_hint(["Setting up curl ...", "done."]) == []


def test_build_command_never_binds_an_apt_conf():
    """Apptainer will not create a bind destination absent from the container,
    so binding an apt.conf failed every build with "destination doesn't exist"."""
    assert "--bind" not in ib.build_command("apptainer", "i.sif", "i.def")


def test_build_command_bounds_the_packing_step_to_the_pod():
    """mksquashfs reads memory and CPU count from the host, not the cgroup.

    Left alone it asks for a quarter of the node's RAM and one thread per node
    CPU, gets killed for it, and the death is reported as "mksquashfs command
    failed: exit status 255" with an empty stderr — because the process that
    exits 255 is the proot wrapper, which cannot report a tracee that never
    exited normally.
    """
    cmd = ib.build_command("apptainer", "i.sif", "i.def")
    args = cmd[cmd.index("--mksquashfs-args") + 1]
    assert "-mem" in args and "-processors" in args
    # One string, not separate argv entries: Apptainer splits it itself.
    assert cmd.count("--mksquashfs-args") == 1


def test_build_env_takes_proot_out_of_the_packing_step():
    """See build_env: proot's opaque 255 is the whole reason this exists.

    Apptainer treats any non-empty value as "set", so the variable has to be
    absent rather than "0" when the bypass is turned off.
    """
    env = ib.build_env({"PATH": "/usr/bin"})
    assert env["APPTAINER_IGNORE_PROOT"] == "1"
    assert env["PATH"] == "/usr/bin"

    os.environ["STUDIO_BUILD_IGNORE_PROOT"] = "0"
    try:
        assert "APPTAINER_IGNORE_PROOT" not in ib.build_env({})
    finally:
        del os.environ["STUDIO_BUILD_IGNORE_PROOT"]


def test_build_env_can_move_the_build_tmpdir():
    os.environ["STUDIO_BUILD_TMPDIR"] = "/scratch/build"
    try:
        env = ib.build_env({})
        assert env["APPTAINER_TMPDIR"] == "/scratch/build"
        assert ib.build_tmpdir(env) == "/scratch/build"
    finally:
        del os.environ["STUDIO_BUILD_TMPDIR"]


def test_build_tmpdir_follows_apptainers_own_order():
    assert ib.build_tmpdir({"APPTAINER_TMPDIR": "/a", "TMPDIR": "/b"}) == "/a"
    assert ib.build_tmpdir({"TMPDIR": "/b"}) == "/b"
    assert ib.build_tmpdir({}) == tempfile.gettempdir()


def test_squashfs_failure_hint_only_fires_for_the_packing_step():
    assert ib.squashfs_failure_hint(["FATAL: %post failed"], "/tmp") == []

    hint = ib.squashfs_failure_hint(
        ["INFO:    Creating SIF file...",
         "FATAL:   While performing build: while creating squashfs: "
         "/usr/libexec/apptainer/bin/mksquashfs command failed: exit status 255:"],
        "/tmp",
    )
    text = "\n".join(hint)
    assert "%post had finished" in text
    assert "/tmp" in text
    assert "STUDIO_BUILD_TMPDIR" in text


def _fake_sif(path, payload_mb=0):
    """A file shaped like a SIF whose filesystem partition is real."""
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 40960)
        fh.write(b"hsqs")                       # squashfs superblock magic
        fh.write(b"\x00" * (payload_mb * 1024 * 1024))


def test_verify_image_accepts_a_real_payload(tmp_path):
    sif = tmp_path / "ok.sif"
    _fake_sif(sif, payload_mb=1)
    assert ib.verify_image(str(sif)) is None


def test_verify_image_rejects_a_build_with_no_filesystem(tmp_path):
    """The exact shape apptainer left behind while reporting success: SIF
    descriptors present, filesystem partition zero-length. `sif list` parses it
    and `ls` looks plausible, so it reached a worker before anyone noticed."""
    sif = tmp_path / "empty.sif"
    sif.write_bytes(b"\x00" * 37461)

    problem = ib.verify_image(str(sif))
    assert problem and "no filesystem" in problem
    # Says what a worker would have said, so the two are recognisably the same
    # failure rather than two mysteries.
    assert "not a valid squashfs image" in problem


def test_verify_image_reports_a_missing_or_truncated_file(tmp_path):
    assert "unreadable" in (ib.verify_image(str(tmp_path / "nope.sif")) or "")
    tiny = tmp_path / "tiny.sif"
    tiny.write_bytes(b"x" * 10)
    assert "10 bytes" in (ib.verify_image(str(tiny)) or "")


def test_verify_image_does_not_reject_a_large_image_of_another_format(tmp_path):
    """ext3 and encrypted payloads carry no squashfs magic. Refusing a
    full-size image on one missing magic number would break those for nothing."""
    sif = tmp_path / "ext3.sif"
    sif.write_bytes(b"\x00" * (6 * 1024 * 1024))
    assert ib.verify_image(str(sif)) is None


def test_find_definition_prefers_apptainer_dir(tmp_path):
    proj = tmp_path / "proj"
    (proj / "Apptainer").mkdir(parents=True)
    (proj / "Apptainer" / "Tools.def").write_text("Bootstrap: docker\n")
    (proj / "Root.def").write_text("Bootstrap: docker\n")

    definition, sif = ib.find_definition(str(proj))
    assert definition.endswith(os.path.join("Apptainer", "Tools.def"))
    # The .sif lands beside the .def so the workflow can reference it by path.
    assert sif == os.path.join(str(proj), "Apptainer", "Tools.sif")

    (proj / "Apptainer" / "Tools.def").unlink()
    definition, sif = ib.find_definition(str(proj))
    assert definition.endswith("Root.def")
    assert sif == os.path.join(str(proj), "Root.sif")


def test_find_definition_missing(tmp_path):
    assert ib.find_definition(str(tmp_path)) is None


def test_find_definition_is_deterministic(tmp_path):
    """Several definitions: the first in sorted order, so rebuilds agree."""
    proj = tmp_path / "proj"
    (proj / "Apptainer").mkdir(parents=True)
    for name in ("zebra.def", "alpha.def", "middle.def"):
        (proj / "Apptainer" / name).write_text("Bootstrap: docker\n")
    definition, _ = ib.find_definition(str(proj))
    assert definition.endswith("alpha.def")


# --- stream_build ---


def _collect_events(gen):
    async def go():
        out = []
        async for chunk in gen:
            if chunk.startswith("data:"):
                out.append(json.loads(chunk[5:]))
        return out

    return asyncio.run(go())


def _build_kwargs(tmp_path, **overrides):
    proj = tmp_path / "proj"
    (proj / "Apptainer").mkdir(parents=True, exist_ok=True)
    (proj / "Apptainer" / "Tools.def").write_text("Bootstrap: docker\nFrom: ubuntu:22.04\n")
    # These tests stand a harmless command in for the builder, so nothing writes
    # the .sif — but the build result is now verified, so one has to be there for
    # the success path to be reachable at all. Pre-create a valid-looking one,
    # which is what a real build would have left.
    _fake_sif(proj / "Apptainer" / "Tools.sif", payload_mb=1)
    kw = dict(
        project_id="proj",
        project_dir=str(proj),
        registry="docker:///kthare10/",
        username="",
        password="",
        tag="latest",
        push=False,
    )
    kw.update(overrides)
    return kw


def test_stream_build_no_builder(monkeypatch, tmp_path):
    monkeypatch.setattr(ib, "detect_builder", lambda: None)
    events = _collect_events(ib.stream_build(**_build_kwargs(tmp_path)))
    assert events[-1]["status"] == "error"
    assert "apptainer" in events[-1]["error"].lower()


def test_stream_build_success_local(monkeypatch, tmp_path):
    """Uses /bin/echo as the 'builder' so the whole pipeline runs for real."""
    monkeypatch.setattr(ib, "detect_builder", lambda: "echo")
    events = _collect_events(ib.stream_build(**_build_kwargs(tmp_path)))

    phases = [e["phase"] for e in events if "phase" in e]
    assert phases == ["build"]
    final = events[-1]
    assert final["status"] == "success"
    # The artifact is a file in the project, not a registry reference.
    assert final["image"].endswith(os.path.join("Apptainer", "Tools.sif"))
    assert final["pushed"] is False
    # echo printed the build args back as a "log line"
    assert any("build --force" in e.get("line", "") for e in events)
    # The user is told how to reference it from the Transformation Catalog.
    assert any('image_site="local"' in e.get("line", "") for e in events)


def test_stream_build_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(ib, "detect_builder", lambda: "false")
    events = _collect_events(ib.stream_build(**_build_kwargs(tmp_path)))
    final = events[-1]
    assert final["status"] == "error"
    assert "Build failed" in final["error"]


def test_stream_build_push_without_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(ib, "detect_builder", lambda: "echo")
    events = _collect_events(
        ib.stream_build(**_build_kwargs(tmp_path, registry="", push=True))
    )
    final = events[-1]
    assert final["status"] == "success"
    assert final["pushed"] is False
    assert any("No container registry configured" in e.get("line", "")
               for e in events)
    # A local .sif is a complete result, not a degraded one.
    assert final["image"].endswith(".sif")


def test_stream_build_push_with_login(monkeypatch, tmp_path):
    """Login gets credentials via stdin (never argv) and push runs."""
    monkeypatch.setattr(ib, "detect_builder", lambda: "echo")
    events = _collect_events(ib.stream_build(**_build_kwargs(
        tmp_path, push=True, username="bob", password="s3cret",
    )))
    phases = [e["phase"] for e in events if "phase" in e]
    assert phases == ["build", "login", "push"]
    final = events[-1]
    assert final["status"] == "success"
    assert final["pushed"] is True
    assert final["ref"] == "docker.io/kthare10/proj:latest"
    # Apptainer publishes the .sif over ORAS, not as an OCI image manifest.
    assert any("oras://docker.io/kthare10/proj:latest" in e.get("line", "")
               for e in events)
    # Password must never appear in any streamed line (echo prints argv).
    assert not any("s3cret" in json.dumps(e) for e in events)
    assert any("--password-stdin" in e.get("line", "") for e in events)


# --- endpoints ---


def test_build_endpoint_streams(client, monkeypatch, tmp_path):
    from routers import workflows as wf

    proj_dir = tmp_path / "csv"
    (proj_dir / "Apptainer").mkdir(parents=True)
    (proj_dir / "Apptainer" / "Tools.def").write_text("Bootstrap: docker\n")
    _fake_sif(proj_dir / "Apptainer" / "Tools.sif", payload_mb=1)

    async def _projects(ws=None):
        return [{
            "project_id": "csv", "project_dir": str(proj_dir),
            "has_container_def": True,
        }]

    monkeypatch.setattr(wf, "discover_projects_async", _projects)
    monkeypatch.setattr(ib, "detect_builder", lambda: "echo")

    res = client.post("/api/workflows/projects/csv/build", json={"push": False})
    assert res.status_code == 200
    assert '"status": "success"' in res.text
    assert "[DONE]" in res.text


def test_build_endpoint_requires_definition(client, monkeypatch):
    from routers import workflows as wf

    async def _projects(ws=None):
        return [{
            "project_id": "csv", "project_dir": "/tmp/x", "has_container_def": False,
        }]

    monkeypatch.setattr(wf, "discover_projects_async", _projects)
    res = client.post("/api/workflows/projects/csv/build", json={})
    assert res.status_code == 400


def test_registry_settings_roundtrip(client):
    # Empty by default.
    body = client.get("/api/settings/registry").json()
    assert body == {"registry": "", "username": "", "has_password": False}

    # Save with password; password is never echoed back.
    body = client.put("/api/settings/registry", json={
        "registry": "docker:///kthare10/", "username": "kthare10",
        "password": "tok123",
    }).json()
    assert body["registry"] == "docker:///kthare10/"
    assert body["username"] == "kthare10"
    assert body["has_password"] is True
    assert "tok123" not in json.dumps(body)

    # Update without password keeps the stored one.
    body = client.put("/api/settings/registry", json={
        "registry": "ghcr.io/pegasus-isi", "username": "kthare10",
    }).json()
    assert body["has_password"] is True

    # Explicit empty string clears it.
    body = client.put("/api/settings/registry", json={
        "registry": "ghcr.io/pegasus-isi", "username": "", "password": "",
    }).json()
    assert body["has_password"] is False


# --- apt sandbox injection ---


def test_needs_apt_fix_detects_apt_use():
    assert ib.needs_apt_fix("%post\n    apt-get update\n")
    assert ib.needs_apt_fix("%post\n    apt install -y curl\n")
    # Already handled, by the skill's template or by hand.
    assert not ib.needs_apt_fix(
        "%post\n    printf 'APT::Sandbox::User \"root\";' > /etc/apt/apt.conf.d/x\n"
        "    apt-get update\n"
    )
    # Nothing to fix for a base that never calls apt.
    assert not ib.needs_apt_fix("%post\n    micromamba install -y numpy\n")


def test_with_apt_fix_inserts_as_the_first_post_step():
    """It must precede apt, not merely appear somewhere in %post."""
    text = "Bootstrap: docker\nFrom: ubuntu:22.04\n\n%post\n    apt-get update\n"
    out = ib.with_apt_fix(text)
    body = out.split("%post", 1)[1]
    assert body.index("APT::Sandbox::User") < body.index("apt-get update")
    # And the result no longer needs fixing, so a rebuild cannot double it.
    assert not ib.needs_apt_fix(out)


def test_with_apt_fix_declines_without_a_post_section():
    assert ib.with_apt_fix("Bootstrap: docker\nFrom: ubuntu:22.04\n") is None


def test_build_uses_a_patched_copy_and_leaves_the_original_alone(monkeypatch, tmp_path):
    """The user's file is never edited; the copy is removed afterwards."""
    monkeypatch.setattr(ib, "detect_builder", lambda: "echo")
    monkeypatch.setattr(ib, "_unprivileged_build", lambda: True)

    proj = tmp_path / "proj"
    (proj / "Apptainer").mkdir(parents=True)
    original = proj / "Apptainer" / "Tools.def"
    source = "Bootstrap: docker\nFrom: ubuntu:22.04\n\n%post\n    apt-get update\n"
    original.write_text(source)
    _fake_sif(proj / "Apptainer" / "Tools.sif", payload_mb=1)

    events = _collect_events(ib.stream_build(
        project_id="proj", project_dir=str(proj), registry="",
        username="", password="", tag="latest", push=False,
    ))

    assert events[-1]["status"] == "success"
    assert original.read_text() == source          # untouched
    assert any("your .def is unchanged" in e.get("line", "") for e in events)
    # No temp file left behind.
    assert not list((proj / "Apptainer").glob(".studio-build-*"))


def test_a_statoverride_is_injected_for_openssh_client():
    """An unprivileged build cannot run openssh-client's postinst chgrp.

    Debian guards it behind `dpkg-statoverride --list`, so registering one
    first makes the package skip the step. Injected into a copy rather than
    required of the definition, for the same reason as the apt setting: the
    constraint belongs to the builder, not the author.
    """
    text = "Bootstrap: docker\nFrom: python:3.8-slim\n\n%post\n    apt-get install -y openssh-client\n"
    assert ib.needs_build_fixes(text) is True
    lines = ib.statoverride_lines(text)
    assert len(lines) == 1
    assert "/usr/bin/ssh-agent" in lines[0]
    # No --update: that flag applies the ownership now, which is what fails.
    assert "--update" not in lines[0]

    out = ib.with_apt_fix(text)
    assert "dpkg-statoverride --add root root 0755 /usr/bin/ssh-agent" in out
    # And it lands before the install it has to precede.
    assert out.index("statoverride") < out.index("apt-get install")


def test_no_statoverride_when_the_package_is_absent():
    text = "%post\n    apt-get install -y curl git\n"
    assert ib.statoverride_lines(text) == []


def test_an_author_who_handles_it_is_left_alone():
    """Don't inject over a definition that already registers the override."""
    text = (
        "%post\n"
        "    dpkg-statoverride --add root root 0755 /usr/bin/ssh-agent\n"
        "    apt-get install -y openssh-client\n"
    )
    assert ib.statoverride_lines(text) == []


def test_a_definition_needing_only_a_statoverride_still_gets_patched():
    """apt is not mentioned, so the apt fix does not apply — but this does."""
    text = "%post\n    dpkg -i /tmp/openssh-client.deb\n"
    assert ib.needs_apt_fix(text) is False
    assert ib.needs_build_fixes(text) is True
    out = ib.with_apt_fix(text)
    assert out is not None
    assert "APT::Sandbox::User" not in out
    assert "ssh-agent" in out


def test_no_patch_at_all_when_nothing_is_needed():
    text = "%post\n    pip install numpy\n"
    assert ib.needs_build_fixes(text) is False
    assert ib.with_apt_fix(text) is None

