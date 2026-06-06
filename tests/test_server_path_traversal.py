import urllib.parse

import pytest
from fastapi.testclient import TestClient

import maida.server as server


def _get_app():
    if hasattr(server, "app"):
        return server.app
    if hasattr(server, "create_app"):
        return server.create_app()
    raise AssertionError("maida.server must expose app or create_app().")


# Payloads that still fit in ONE path segment (so they definitely hit /api/runs/{run_id})
SEGMENT_ONLY = [
    "not-a-uuid",
    "00000000-0000-0000-0000-00000000000g",
    "00000000-0000-0000-0000-000000000000..",
    "%2e",  # "."
    "%2e%2e",  # ".."
    "%2E%2E",
    "%252e%252e",  # double-encoded ".."
]

# Payloads that *may* get treated as path separators (router might 404 before handler)
MAY_BREAK_ROUTING = [
    "../etc/passwd",
    "..%2f..%2fetc%2fpasswd",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "..%252f..%252fetc%252fpasswd",  # double-encoded
    "..\\..\\Windows\\win.ini",
    "%2e%2e%5c%2e%2e%5cWindows%5cwin.ini",
]

ENDPOINTS = [
    "/api/runs/{rid}",
    "/api/runs/{rid}/spans",
    "/api/runs/{rid}/paths",
    "/api/runs/{rid}/rename",
]


@pytest.mark.parametrize("rid_raw", SEGMENT_ONLY)
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_traversal_like_run_ids_rejected_with_400(
    monkeypatch, tmp_path, rid_raw, endpoint
):
    monkeypatch.setenv("MAIDA_DATA_DIR", str(tmp_path))

    app = _get_app()
    client = TestClient(app)

    # Keep literal % sequences intact in the URL we send.
    rid = rid_raw
    r = client.get(endpoint.format(rid=rid))

    assert r.status_code == 400


@pytest.mark.parametrize("rid_raw", MAY_BREAK_ROUTING)
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_traversal_payloads_never_succeed(monkeypatch, tmp_path, rid_raw, endpoint):
    monkeypatch.setenv("MAIDA_DATA_DIR", str(tmp_path))

    app = _get_app()
    client = TestClient(app)

    # Ensure it's a single URL segment on the client side (slashes encoded)
    rid = urllib.parse.quote(
        rid_raw, safe="%"
    )  # keep any %xx sequences you already provided
    r = client.get(endpoint.format(rid=rid))

    # Depending on URL decoding + routing, this can be 400/404/422 — but must never be 200.
    assert r.status_code != 200
