"""Tests for the workflow Marketplace (PegasusHub registry + Pegasus-ISI, GitHub)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

import routers.marketplace as mkt

# The real search, captured before the autouse fixture replaces it.
_real_fetch_topic_repos = mkt._fetch_topic_repos


def _reset():
    mkt._cache["data"] = None
    mkt._cache["ts"] = 0.0


def _search_item(name: str, *, topics=("pegasus-studio-example",), description="", **extra):
    """One repo as GitHub's repository-search API returns it."""
    return {
        "name": name,
        "html_url": f"https://github.com/pegasus-isi/{name}",
        "description": description,
        "topics": list(topics),
        "archived": False,
        "private": False,
        "license": {"name": "Apache License 2.0"},
        **extra,
    }


# What the org listed under the marker topic when the hand-maintained list
# was replaced; the tests below were written against these six.
_TAGGED = [
    _search_item("quickstart-workflow", topics=("pegasus-studio-example", "tutorial")),
    _search_item("soilmoisture-workflow", topics=("pegasus-studio-example", "earth-science")),
    _search_item("airquality-workflow", topics=("pegasus-studio-example", "earth-science")),
    _search_item("earthquake-workflow", topics=("pegasus-studio-example", "earth-science")),
    _search_item("crophealth-workflow", topics=("pegasus-studio-example", "machine-learning")),
    _search_item(
        "variant-calling-pegasus-workflow", topics=("pegasus-studio-example", "bioinformatics")
    ),
]


@pytest.fixture(autouse=True)
def _org_repos(monkeypatch):
    """Never reach GitHub search from tests; serve the six tagged repos."""
    async def fake_topic_repos():
        return mkt._from_search(_TAGGED)

    monkeypatch.setattr(mkt, "_fetch_topic_repos", fake_topic_repos)
    monkeypatch.setattr(mkt, "_last_topic_repos", [])


def test_catalog_merges_registry_and_org_repos_deduped(client, monkeypatch):
    # _fetch_registry returns already-normalized entries (repo_url, source, …).
    async def fake_registry():
        return [
            mkt._normalize(
                {
                    "id": "variant-calling",
                    "name": "variant-calling",
                    # Duplicate of an org repo (different case + .git) -> deduped.
                    "repo_url": "https://GitHub.com/pegasus-isi/variant-calling-pegasus-workflow.git",
                    "topic": "bioinformatics",
                },
                "PegasusHub",
            ),
            mkt._normalize(
                {
                    "id": "orcasound",
                    "name": "orcasound-workflow",
                    "github_repo": "https://github.com/pegasus-isi/orcasound-workflow",
                    "topic": "machine learning",
                },
                "PegasusHub",
            ),
        ]

    monkeypatch.setattr(mkt, "_fetch_registry", fake_registry)
    _reset()

    body = client.get("/api/marketplace").json()
    wfs = {w["id"]: w for w in body["workflows"]}

    # All 6 org repos + 1 unique registry = 7; the dup collapses onto the org one.
    assert body["count"] == 7
    assert "orcasound" in wfs and wfs["orcasound"]["source"] == "PegasusHub"
    # variant-calling appears once, as the Pegasus-ISI org entry.
    vc = [w for w in body["workflows"] if "variant-calling" in w["repo_url"].lower()]
    assert len(vc) == 1
    assert vc[0]["source"] == "Pegasus-ISI"
    # The specific repos the user asked for are all present.
    ids = set(wfs)
    for expected in ("soilmoisture", "crophealth", "earthquake", "airquality",
                     "quickstart", "variant-calling-pegasus"):
        assert expected in ids


def test_catalog_leads_with_featured_examples(client, monkeypatch):
    """The catalog opens with quickstart and then the portable-site examples,
    in order, ahead of the other org/registry entries."""
    async def fake_registry():
        return [
            mkt._normalize(
                {
                    "id": "orcasound",
                    "name": "orcasound-workflow",
                    "github_repo": "https://github.com/pegasus-isi/orcasound-workflow",
                },
                "PegasusHub",
            ),
        ]

    monkeypatch.setattr(mkt, "_fetch_registry", fake_registry)
    _reset()

    ids = [w["id"] for w in client.get("/api/marketplace").json()["workflows"]]
    assert ids[:4] == ["quickstart", "earthquake", "airquality", "crophealth"]
    # Non-featured entries still follow (alphabetical within their group).
    assert ids[4:] == ["soilmoisture", "variant-calling-pegasus", "orcasound"]


def test_registry_failure_still_lists_org_repos(client, monkeypatch):
    async def empty_registry():
        return []

    monkeypatch.setattr(mkt, "_fetch_registry", empty_registry)
    _reset()

    body = client.get("/api/marketplace").json()
    assert body["count"] == len(_TAGGED)
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

    res = client.post("/api/marketplace/soilmoisture/add")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "added"
    assert data["project_id"] == "soilmoisture"
    assert "soilmoisture-workflow" in data["repo_url"]


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

    assert client.post("/api/marketplace/soilmoisture/add").status_code == 200
    args = captured["args"]
    sep = args.index("--")
    # Everything after "--" is positional: <url> <dir>.
    assert args[sep + 1].startswith("https://")
    assert captured["env"].get("GIT_ALLOW_PROTOCOL") == "http:https"


# --- org repos come from the marker GitHub topic ---


def test_search_items_become_catalog_entries():
    entries = mkt._from_search([
        _search_item(
            "earthquake-workflow",
            topics=("pegasus-studio-example", "earth-science", "seismology"),
            description="USGS earthquake analysis",
        ),
    ])
    assert len(entries) == 1
    e = entries[0]
    # The id the hand-maintained list used, so existing clones and the
    # featured order are unaffected by the move to topics.
    assert e["id"] == "earthquake"
    assert e["name"] == "earthquake-workflow"
    assert e["source"] == "Pegasus-ISI"
    assert e["description"] == "USGS earthquake analysis"
    assert e["repo_url"] == "https://github.com/pegasus-isi/earthquake-workflow"
    assert e["license"] == "Apache License 2.0"
    # The marker topic is not a subject: as a chip it would match everything.
    assert e["topics"] == ["earth science", "seismology"]


def test_archived_private_and_unsafe_repos_are_skipped():
    entries = mkt._from_search([
        _search_item("old-workflow", archived=True),
        _search_item("secret-workflow", private=True),
        _search_item("evil-workflow", html_url="ext::sh -c touch% /tmp/pwned"),
        _search_item("kept-workflow"),
        "not a dict",
    ])
    assert [e["id"] for e in entries] == ["kept"]


class _FakeResponse:
    def __init__(self, payload=None, error: Exception | None = None):
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._payload


def _fake_client(monkeypatch, response, seen=None):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, headers=None):
            if seen is not None:
                seen.append(params)
            return response

    monkeypatch.setattr(mkt.httpx, "AsyncClient", FakeClient)


def test_topic_search_queries_the_org_and_topic(monkeypatch):
    seen: list = []
    _fake_client(
        monkeypatch,
        _FakeResponse({"items": [_search_item("quickstart-workflow")]}),
        seen,
    )
    entries = asyncio.run(_real_fetch_topic_repos())
    assert [e["id"] for e in entries] == ["quickstart"]
    assert seen[0]["q"] == "org:pegasus-isi topic:pegasus-studio-example"


def test_topic_search_failure_reuses_the_last_good_list(monkeypatch):
    """A rate limit must not empty the org half of the catalog for an hour."""
    _fake_client(
        monkeypatch,
        _FakeResponse({"items": [_search_item("quickstart-workflow")]}),
    )
    assert [e["id"] for e in asyncio.run(_real_fetch_topic_repos())] == ["quickstart"]

    request = httpx.Request("GET", mkt.GITHUB_SEARCH_URL)
    _fake_client(
        monkeypatch,
        _FakeResponse(
            error=httpx.HTTPStatusError(
                "rate limited",
                request=request,
                response=httpx.Response(403, request=request),
            )
        ),
    )
    assert [e["id"] for e in asyncio.run(_real_fetch_topic_repos())] == ["quickstart"]
