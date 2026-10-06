"""Offline Langfuse transport and authentication tests."""

from __future__ import annotations
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
import pytest
from maida.integrations.langfuse import LangfuseClient, LangfuseImportError, LangfuseInputError
from tests.support.langfuse import _FakeResponse


def test_client_follows_cursor_pagination(monkeypatch):
    requests = []
    responses = [
        {"data": [{"id": "first"}], "meta": {"cursor": "next-page"}},
        {"data": [{"id": "second"}], "meta": {}},
    ]

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _FakeResponse(responses.pop(0))

    monkeypatch.setitem(LangfuseClient._get_json.__globals__, "urlopen", fake_urlopen)
    client = LangfuseClient(
        base_url="https://example.langfuse.test",
        public_key="pk-public",
        secret_key="sk-private",
        timeout=7,
    )

    rows = client.fetch_observations({"traceId": "trace-one"})

    assert [row["id"] for row in rows] == ["first", "second"]
    assert len(requests) == 2
    second_query = parse_qs(urlsplit(requests[1][0].full_url).query)
    assert second_query["cursor"] == ["next-page"]
    assert requests[0][1] == 7
    assert requests[0][0].get_header("Authorization").startswith("Basic ")


def test_client_rejects_repeated_cursor(monkeypatch):
    def fake_urlopen(_request, timeout):
        assert timeout == 5
        return _FakeResponse({"data": [], "meta": {"cursor": "same"}})

    monkeypatch.setitem(LangfuseClient._get_json.__globals__, "urlopen", fake_urlopen)
    client = LangfuseClient("https://example.test", "public", "secret")

    with pytest.raises(LangfuseImportError, match="repeated pagination cursor"):
        client.fetch_observations({})


def test_client_rejects_base_url_paths_and_redacts_auth_errors(monkeypatch):
    with pytest.raises(LangfuseInputError, match=r"http\(s\) origin"):
        LangfuseClient("https://example.test/langfuse", "public", "secret")

    def unauthorized(request, timeout):
        assert timeout == 5
        raise HTTPError(request.full_url, 401, "unauthorized", None, None)

    monkeypatch.setitem(LangfuseClient._get_json.__globals__, "urlopen", unauthorized)
    client = LangfuseClient("https://example.test", "public-value", "secret-value")

    with pytest.raises(LangfuseImportError) as raised:
        client.fetch_observations({})

    assert "authentication failed" in str(raised.value)
    assert "public-value" not in str(raised.value)
    assert "secret-value" not in str(raised.value)
