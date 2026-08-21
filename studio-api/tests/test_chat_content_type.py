"""Chat handlers parse raw JSON; they must refuse bodies not declared as
``application/json``.

``request.json()`` ignores Content-Type, so a form post or a ``text/plain``
``fetch`` from any page the user happens to have open — neither needs a CORS
preflight — was accepted exactly as if the UI had sent it: start a turn with
attacker text, stop every in-flight turn, answer someone's approval prompt
(PAS-SEC-01 / NEW-05).
"""

from __future__ import annotations

import pytest

# Every chat route that reads its body with request.json().
ENDPOINTS = [
    ("POST", "/api/chat/stream"),
    ("POST", "/api/chat/stop"),
    ("POST", "/api/chat/permission"),
    ("POST", "/api/chat/feedback"),
    ("PUT", "/api/chat/engine"),
    ("PUT", "/api/chat/command-approval"),
]

ENDPOINT_IDS = [f"{m} {p}" for m, p in ENDPOINTS]


@pytest.mark.parametrize("method,path", ENDPOINTS, ids=ENDPOINT_IDS)
def test_rejects_text_plain(client, method: str, path: str) -> None:
    """A text/plain body is a CORS simple request — refused before the handler
    sees it, so the JSON inside it never takes effect."""
    resp = client.request(
        method,
        path,
        content=b'{"all": true}',
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 415
    assert "application/json" in resp.json()["detail"]


@pytest.mark.parametrize("method,path", ENDPOINTS, ids=ENDPOINT_IDS)
def test_rejects_form_encoded(client, method: str, path: str) -> None:
    """The other simple-request shape: an HTML form auto-submitted cross-site."""
    resp = client.request(method, path, data={"all": "true"})
    assert resp.status_code == 415


@pytest.mark.parametrize("method,path", ENDPOINTS, ids=ENDPOINT_IDS)
def test_rejects_missing_content_type(client, method: str, path: str) -> None:
    """`fetch` with a Blob body and no type sends no Content-Type at all."""
    resp = client.request(method, path, content=b"{}")
    assert resp.status_code == 415


@pytest.mark.parametrize("method,path", ENDPOINTS, ids=ENDPOINT_IDS)
def test_rejects_invalid_json(client, method: str, path: str) -> None:
    resp = client.request(
        method,
        path,
        content=b"{nope",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid JSON body"


@pytest.mark.parametrize("method,path", ENDPOINTS, ids=ENDPOINT_IDS)
def test_rejects_non_object_json(client, method: str, path: str) -> None:
    """A list is valid JSON but has no .get() — it used to reach the handler
    and 500."""
    resp = client.request(method, path, json=[1, 2])
    assert resp.status_code == 400
    assert resp.json()["detail"] == "JSON body must be an object"


def test_stop_all_via_form_leaves_streams_alive(client, monkeypatch) -> None:
    """The concrete damage the check prevents: a cross-site form post to
    /stop with all=true killed every in-flight turn in the container."""
    import routers.chat as chat

    closed: list[str] = []

    class FakeClient:
        async def aclose(self) -> None:
            closed.append("rid")

    monkeypatch.setitem(chat._active_clients, "rid", FakeClient())

    resp = client.post("/api/chat/stop", data={"all": "true"})
    assert resp.status_code == 415
    assert "rid" in chat._active_clients
    assert closed == []

    # The same request the UI makes still works.
    resp = client.post("/api/chat/stop", json={"all": True})
    assert resp.status_code == 200
    assert resp.json()["cleared"] is True
    assert closed == ["rid"]
    assert "rid" not in chat._active_clients


def test_json_with_charset_parameter_is_accepted(client) -> None:
    """Only the media type is compared: `application/json; charset=utf-8` is
    what several HTTP clients send and must not be refused."""
    resp = client.post(
        "/api/chat/stop",
        content=b"{}",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "noop"


@pytest.mark.parametrize("method,path", ENDPOINTS, ids=ENDPOINT_IDS)
def test_json_body_still_reaches_the_handler(client, method: str, path: str) -> None:
    """The shape the UI sends gets through: `{}` is refused (if at all) by the
    handler's own validation, never by the Content-Type or parse checks."""
    resp = client.request(method, path, json={})
    assert resp.status_code != 415
    if resp.status_code == 400:
        assert resp.json()["detail"] not in (
            "Invalid JSON body",
            "JSON body must be an object",
        )
