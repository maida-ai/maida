"""Explicitly configured, best-effort usage counts; no default collector."""

import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from fastapi import FastAPI, Request as WebRequest, Response

from maida import __version__


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_payload(verdict: str, repo_id: str, pr: bool) -> dict:
    if verdict not in {"pass", "fail", "inconclusive"}:
        raise ValueError("Unknown verdict")
    if re.fullmatch(r"[0-9a-f]{64}", repo_id) is None:
        raise ValueError("Expected a random repository identifier")
    return {
        "schema_version": 1,
        "version": __version__,
        "repo_id": repo_id,
        "date": datetime.now(timezone.utc).date().isoformat(),
        "pr": pr,
        "verdict_counts": {
            key: int(key == verdict) for key in ("pass", "fail", "inconclusive")
        },
    }


def report_usage(verdict: str) -> str:
    """Never affect a gate verdict, retry, discover identity, or send by default."""
    if os.environ.get("MAIDA_USAGE_OPT_IN") != "1":
        return "Usage ping: disabled"
    try:
        endpoint = os.environ.get("MAIDA_USAGE_ENDPOINT", "")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid collector URL")
        payload = make_payload(
            verdict,
            os.environ.get("MAIDA_USAGE_REPO_ID", ""),
            os.environ.get("GITHUB_EVENT_NAME")
            in {"pull_request", "pull_request_target"},
        )
        request = Request(
            endpoint,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Avoid proxy credentials and redirects to an unintended recipient.
        with build_opener(ProxyHandler({}), _NoRedirect()).open(
            request, timeout=1
        ) as response:
            response.read(1)
        return "Usage ping: opted in; sent"
    except Exception:
        # Error text can include URLs or credentials: keep it out of gate output.
        return "Usage ping: opted in; unavailable"


def _validate_payload(payload: object) -> bool:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "version",
        "repo_id",
        "date",
        "pr",
        "verdict_counts",
    }:
        return False
    counts = payload["verdict_counts"]
    try:
        if (
            not isinstance(payload["date"], str)
            or re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload["date"]) is None
        ):
            return False
        date.fromisoformat(payload["date"])
        return (
            type(payload["schema_version"]) is int
            and payload["schema_version"] == 1
            and isinstance(payload["version"], str)
            and re.fullmatch(r"[A-Za-z0-9.+_-]{1,100}", payload["version"]) is not None
            and isinstance(payload["repo_id"], str)
            and re.fullmatch(r"[0-9a-f]{64}", payload["repo_id"]) is not None
            and type(payload["pr"]) is bool
            and isinstance(counts, dict)
            and set(counts) == {"pass", "fail", "inconclusive"}
            and all(type(n) is int and n in (0, 1) for n in counts.values())
            and sum(counts.values()) == 1
        )
    except (ValueError, TypeError):
        return False


def create_receiver(log_path: Path, *, enabled: bool = False):
    """Build a disabled-by-default append-only receiver, separate from the viewer."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/usage")
    async def receive(request: WebRequest):
        if not enabled:
            return Response(status_code=503)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 2048:
                return Response(status_code=413)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return Response(status_code=422)
        if not _validate_payload(payload):
            return Response(status_code=422)
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
        # A single append operation per event; never store request headers/IP.
        fd = os.open(log_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            if os.write(fd, line) != len(line):
                raise OSError("Incomplete usage record")
        finally:
            os.close(fd)
        return Response(status_code=204)

    return app
