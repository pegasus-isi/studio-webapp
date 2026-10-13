"""Tests for the workflow Marketplace (PegasusHub registry + Pegasus-ISI, GitHub)."""

from __future__ import annotations

import asyncio

import routers.marketplace as mkt


def _reset():
    mkt._cache["data"] = None
    mkt._cache["ts"] = 0.0


def test_catalog_is_curated_trio_only(client, monkeypatch):
    # Hackathon scope: the catalog is exactly quickstart, somospie,
    # earthquake. The live registry is intentionally not consulted.
    async def fake_registry():
        raise AssertionError("registry must not be fetched in curated-only mode")

    monkeypatch.setattr(mkt, "_fetch_registry", fake_registry)
    _reset()

    body = client.get("/api/marketplace").json()
    wfs = {w["id"]: w for w in body["workflows"]}

    assert body["count"] == 3
    assert [w["id"] for w in body["workflows"]] == [
        "quickstart",
        "somospie",
        "earthquake",
    ]
    assert all(w["source"] == "Pegasus-ISI" for w in body["workflows"])
    assert (
        wfs["somospie"]["repo_url"]
        == "https://github.com/TauferLab/somospie-pegasus-workflow"
    )
    assert "SOMOSPIE" in wfs["somospie"]["description"]


def test_catalog_leads_with_featured_examples(client, monkeypatch):
    """The catalog opens with the hackathon trio, in order."""
    async def fake_registry():
        raise AssertionError("registry must not be fetched in curated-only mode")

    monkeypatch.setattr(mkt, "_fetch_registry", fake_registry)
    _reset()

    ids = [w["id"] for w in client.get("/api/marketplace").json()["workflows"]]
    assert ids == ["quickstart", "somospie", "earthquake"]


def test_registry_failure_falls_back_to_curated(client, monkeypatch):
    async def empty_registry():
        return []

    monkeypatch.setattr(mkt, "_fetch_registry", empty_registry)
    _reset()

    body = client.get("/api/marketplace").json()
    assert body["count"] == len(mkt._CURATED)
    assert all(w["source"] == "Pegasus-ISI" for w in body["workflows"])


def test_add_clones_selected_repo(client, monkeypatch, tmp_path):
    async def fake_registry():
        return []

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(mkt, "_fetch_registry", fake_registry)
    monkeypatch.setattr(mkt, "WORKFLOWS_DIR", str(tmp_path / "workflows"))
    monkeypatch.setattr(mkt.asyncio, "create_subprocess_exec", fake_exec)
    _reset()

    res = client.post("/api/marketplace/somospie/add")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "added"
    assert data["project_id"] == "somospie"
    assert "somospie-pegasus-workflow" in data["repo_url"]


def test_add_rejects_bad_id(client):
    assert client.post("/api/marketplace/..%2Fetc/add").status_code in (400, 404)
    assert client.post("/api/marketplace/bad id/add").status_code in (400, 404)


def test_add_unknown_id(client, monkeypatch):
    async def fake_registry():
        return []

    monkeypatch.setattr(mkt, "_fetch_registry", fake_registry)
    _reset()
    assert client.post("/api/marketplace/nonexistent/add").status_code == 404


def test_add_conflict_when_exists(client, monkeypatch, tmp_path):
    async def fake_registry():
        return []

    wdir = tmp_path / "workflows"
    (wdir / "quickstart").mkdir(parents=True)
    monkeypatch.setattr(mkt, "_fetch_registry", fake_registry)
    monkeypatch.setattr(mkt, "WORKFLOWS_DIR", str(wdir))
    _reset()

    assert client.post("/api/marketplace/quickstart/add").status_code == 409


# --- SEC-1: repo URLs from the community-editable registry are untrusted ---


def test_normalize_rejects_non_http_repo_urls():
    """ext:: executes a shell, file:: reads local paths, and a leading "-"
    is parsed as a git flag — none may ever enter the catalog."""
    for url in (
        "ext::sh -c 'touch /tmp/pwned'",
        "file:///etc",
        "--upload-pack=touch /tmp/pwned",
        "git@github.com:x/y.git",
        "",
    ):
        assert mkt._normalize({"repo_url": url, "id": "x"}, "PegasusHub") is None

    ok = mkt._normalize(
        {"repo_url": "https://github.com/x/y", "id": "x"}, "PegasusHub"
    )
    assert ok is not None


def test_comma_joined_topics_split_into_separate_tags():
    """The registry's `topic` is free text (#314).

    "Machine-Learning, Optuna, Pytorch-Cnn, Transfer-Learning" arrived as one
    filter chip that matched a single workflow, while "Machine Learning" and
    "Machine-Learning" sat beside it as two more chips for the same subject.
    """
    entry = mkt._normalize(
        {
            "repo_url": "https://github.com/x/mask-detection-workflow",
            "topic": "Machine-Learning, Optuna, Pytorch-Cnn, Transfer-Learning",
        },
        "PegasusHub",
    )
    assert entry is not None
    assert entry["topics"] == [
        "machine learning",
        "optuna",
        "pytorch cnn",
        "transfer learning",
    ]
    # `topic` stays a plain string for any client still reading it.
    assert entry["topic"] == "machine learning"


def test_topic_case_and_hyphen_variants_fold_together():
    variants = ["Machine Learning", "machine-learning", "  MACHINE_LEARNING  "]
    folded = {
        mkt._normalize(
            {"repo_url": "https://github.com/x/y", "topic": v}, "PegasusHub"
        )["topic"]
        for v in variants
    }
    assert folded == {"machine learning"}


def test_missing_topic_yields_no_tags_rather_than_an_empty_one():
    entry = mkt._normalize({"repo_url": "https://github.com/x/y"}, "PegasusHub")
    assert entry["topics"] == []
    assert entry["topic"] == ""


def test_name_falls_back_to_a_readable_title_not_the_repo_slug():
    cases = {
        "https://github.com/x/variant-calling-pegasus-workflow": "Variant Calling",
        "https://github.com/x/lung-instance-segmentation-workflow": "Lung Instance Segmentation",
        "https://github.com/x/1000genome-workflow": "1000genome",
        "https://github.com/x/montage-workflow-v3": "Montage Workflow V3",
    }
    for url, expected in cases.items():
        entry = mkt._normalize({"repo_url": url}, "PegasusHub")
        assert entry["name"] == expected, url


def test_an_explicit_registry_name_is_never_overridden():
    entry = mkt._normalize(
        {"repo_url": "https://github.com/x/y-workflow", "name": "Orcasound workflow"},
        "PegasusHub",
    )
    assert entry["name"] == "Orcasound workflow"


async def _enrich(entries, monkeypatch, responses):
    """Run _enrich_descriptions with GitHub stubbed out."""

    class FakeResp:
        def __init__(self, payload):
            self.status_code = 200 if payload is not None else 404
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return FakeResp(responses.get(url))

    monkeypatch.setenv("STUDIO_MARKETPLACE_ENRICH", "1")
    monkeypatch.setattr(mkt.httpx, "AsyncClient", lambda **kw: FakeClient())
    await mkt._enrich_descriptions(entries)


def test_blank_descriptions_are_filled_from_github(monkeypatch):
    """Roughly half the registry omits `description` (#314).

    The repo already carries a one-liner — the catalog just was not asking.
    """
    entries = [
        {
            "repo_url": "https://github.com/pegasushub/1000genome-workflow",
            "description": "",
            "topics": [],
        },
        {
            "repo_url": "https://github.com/pegasus-isi/quickstart-workflow",
            "description": "Already has one, must not be overwritten.",
            "topics": ["tutorial"],
        },
    ]
    asyncio.run(
        _enrich(
            entries,
            monkeypatch,
            {
                "https://api.github.com/repos/pegasushub/1000genome-workflow": {
                    "description": "Population-scale variant analysis.",
                    "topics": ["Bioinformatics", "Genomics"],
                }
            },
        )
    )
    assert entries[0]["description"] == "Population-scale variant analysis."
    # Repo topics are owner-curated and cleaner than the registry's free text.
    assert entries[0]["topics"] == ["bioinformatics", "genomics"]
    assert entries[1]["description"] == "Already has one, must not be overwritten."


def test_enrichment_degrades_to_no_description_rather_than_failing(monkeypatch):
    """A rate-limit, a timeout or a private repo must not break the catalog.

    A card with no description is honest; a placeholder repeated down the grid
    looks like content and carries none.
    """
    entries = [{"repo_url": "https://github.com/x/private", "description": ""}]
    asyncio.run(_enrich(entries, monkeypatch, {}))  # every lookup 404s
    assert entries[0]["description"] == ""


def test_enrichment_is_skipped_when_disabled(monkeypatch):
    entries = [{"repo_url": "https://github.com/x/y", "description": ""}]

    def explode(**kw):
        raise AssertionError("must not reach the network when disabled")

    monkeypatch.setenv("STUDIO_MARKETPLACE_ENRICH", "0")
    monkeypatch.setattr(mkt.httpx, "AsyncClient", explode)
    asyncio.run(mkt._enrich_descriptions(entries))
    assert entries[0]["description"] == ""


def test_non_github_repos_are_left_alone(monkeypatch):
    entries = [{"repo_url": "https://gitlab.com/x/y", "description": ""}]
    asyncio.run(_enrich(entries, monkeypatch, {}))
    assert entries[0]["description"] == ""
    assert mkt._gh_api_url("https://gitlab.com/x/y") is None


def test_add_refuses_non_http_repo_url(client, monkeypatch, tmp_path):
    """Defense in depth behind the _normalize filter: even if a bad URL
    reached the catalog, add must refuse before ever exec'ing git."""

    async def fake_catalog(force=False):
        return [{
            "id": "evil", "name": "evil",
            "repo_url": "ext::sh -c 'touch /tmp/pwned'",
        }]

    called = []

    async def fake_exec(*args, **kwargs):
        called.append(args)
        raise AssertionError("git must not run for a non-http(s) URL")

    monkeypatch.setattr(mkt, "_catalog", fake_catalog)
    monkeypatch.setattr(mkt, "WORKFLOWS_DIR", str(tmp_path / "workflows"))
    monkeypatch.setattr(mkt.asyncio, "create_subprocess_exec", fake_exec)

    res = client.post("/api/marketplace/evil/add")
    assert res.status_code == 400
    assert called == []


def test_add_uses_separator_and_protocol_pin(client, monkeypatch, tmp_path):
    """The clone must pass the URL after "--" (so it can't read as a flag)
    and pin git's allowed transports to http(s)."""

    async def fake_registry():
        return []

    captured = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env") or {}
        return FakeProc()

    monkeypatch.setattr(mkt, "_fetch_registry", fake_registry)
    monkeypatch.setattr(mkt, "WORKFLOWS_DIR", str(tmp_path / "workflows"))
    monkeypatch.setattr(mkt.asyncio, "create_subprocess_exec", fake_exec)
    _reset()

    assert client.post("/api/marketplace/somospie/add").status_code == 200
    args = captured["args"]
    sep = args.index("--")
    # Everything after "--" is positional: <url> <dir>.
    assert args[sep + 1].startswith("https://")
    assert captured["env"].get("GIT_ALLOW_PROTOCOL") == "http:https"
