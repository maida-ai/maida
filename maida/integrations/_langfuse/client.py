"""Read-only langfuse import: client."""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from maida.integrations._langfuse.observations import _deduplicate_observations
from maida.integrations._langfuse.types import _OBSERVATION_FIELDS, LangfuseImportError, LangfuseInputError


class LangfuseClient:
    """Small stdlib client for Langfuse's read-only observations API."""

    def __init__(
        self,
        base_url: str,
        public_key: str,
        secret_key: str,
        timeout: float = 5.0,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise LangfuseInputError(
                "Langfuse base URL must be an http(s) origin without credentials, a query, or a fragment"
            )
        if not public_key or not secret_key:
            raise LangfuseInputError("Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY before importing")
        if timeout <= 0:
            raise LangfuseInputError("LANGFUSE_TIMEOUT must be greater than zero")
        self.base_url = base_url.rstrip("/")
        self.public_key = public_key
        self.secret_key = secret_key
        self.timeout = timeout

    def fetch_observations(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch every cursor page for a single observations query."""
        rows: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None
        while True:
            page_params = {
                "limit": 1000,
                "fields": _OBSERVATION_FIELDS,
                **params,
            }
            if cursor is not None:
                page_params["cursor"] = cursor
            payload = self._get_json(page_params)
            data = payload.get("data")
            meta = payload.get("meta")
            if not isinstance(data, list) or not isinstance(meta, dict):
                raise LangfuseImportError("Langfuse observations response must contain data[] and meta{}")
            for item in data:
                if not isinstance(item, dict):
                    raise LangfuseImportError("Langfuse observations response contains a non-object row")
                rows.append(item)
            next_cursor = meta.get("cursor")
            if next_cursor is None or next_cursor == "":
                break
            if not isinstance(next_cursor, str):
                raise LangfuseImportError("Langfuse observations cursor must be a string")
            if next_cursor in seen_cursors:
                raise LangfuseImportError("Langfuse returned a repeated pagination cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return _deduplicate_observations(rows)

    def _get_json(self, params: dict[str, Any]) -> dict[str, Any]:
        query = urlencode(params, doseq=True)
        url = f"{self.base_url}/api/public/v2/observations?{query}"
        credentials = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode("utf-8")).decode("ascii")
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {credentials}",
                "X-Langfuse-Sdk-Name": "maida",
            },
            method="GET",
        )
        host = urlsplit(self.base_url).netloc
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise LangfuseImportError(f"Langfuse authentication failed at {host} (HTTP {exc.code})") from exc
            raise LangfuseImportError(f"Langfuse request failed at {host} (HTTP {exc.code})") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise LangfuseImportError(f"Could not reach Langfuse at {host}: {type(exc).__name__}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LangfuseImportError(f"Langfuse returned malformed JSON from {host}") from exc
        if not isinstance(payload, dict):
            raise LangfuseImportError("Langfuse response root must be an object")
        return payload


def client_from_environment(base_url: str | None = None) -> LangfuseClient:
    """Create a Langfuse client from the SDK-compatible environment variables."""
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    resolved_base_url = (
        (base_url or "").strip()
        or os.environ.get("LANGFUSE_BASE_URL", "").strip()
        or os.environ.get("LANGFUSE_HOST", "").strip()
        or "https://cloud.langfuse.com"
    )
    timeout_text = os.environ.get("LANGFUSE_TIMEOUT", "5").strip()
    try:
        timeout = float(timeout_text)
    except ValueError as exc:
        raise LangfuseInputError("LANGFUSE_TIMEOUT must be a number") from exc
    return LangfuseClient(
        base_url=resolved_base_url,
        public_key=public_key,
        secret_key=secret_key,
        timeout=timeout,
    )
