"""OVH API client with HMAC-SHA1 request signing."""

from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx

from config import Config


class OvhApiError(Exception):
    """Raised on OVH API errors."""

    def __init__(self, status: int, message: str, url: str = ""):
        self.status = status
        self.url = url
        super().__init__(f"OVH API {status}: {message} [{url}]")


class OvhClient:
    """Lightweight async OVH API client implementing their HMAC signature scheme.

    Auth docs: https://help.ovhcloud.com/csm/en-gb-api-getting-started-ovhcloud-api
    """

    def __init__(self, config: Config, anonymous: bool = False) -> None:
        self._anonymous = anonymous
        self._app_key = config.ovh_app_key
        self._app_secret = config.ovh_app_secret
        self._consumer_key = config.ovh_consumer_key
        self._base_url = config.ovh_api_base
        self._time_delta: int | None = None
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            headers={"Content-Type": "application/json"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    # ── Time sync ──────────────────────────────────────────────

    async def _sync_time(self) -> None:
        """Get OVH server time and compute delta for signature."""
        resp = await self._http.get(f"{self._base_url}/auth/time")
        resp.raise_for_status()
        server_time = int(resp.text)
        self._time_delta = server_time - int(time.time())

    def _now(self) -> int:
        return int(time.time()) + (self._time_delta or 0)

    # ── Signature ──────────────────────────────────────────────

    def _sign(self, method: str, url: str, body: str, timestamp: str) -> str:
        """Compute $1$<SHA1> signature per OVH spec."""
        raw = "+".join([
            self._app_secret,
            self._consumer_key,
            method.upper(),
            url,
            body,
            timestamp,
        ])
        h = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return f"$1${h}"

    # ── HTTP helpers ───────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        if self._anonymous:
            resp = await self._http.request(method, url, json=data, params=params)
            return self._result(resp, url)

        if self._time_delta is None:
            await self._sync_time()

        # Build full URL with query string for signature
        request = self._http.build_request(
            method, url, json=data, params=params,
        )
        full_url = str(request.url)
        body = request.content.decode() if request.content else ""
        timestamp = str(self._now())

        sig = self._sign(method, full_url, body, timestamp)

        headers = {
            "X-Ovh-Application": self._app_key,
            "X-Ovh-Consumer": self._consumer_key,
            "X-Ovh-Timestamp": timestamp,
            "X-Ovh-Signature": sig,
        }

        resp = await self._http.request(
            method, url, json=data, params=params, headers=headers,
        )
        return self._result(resp, full_url)

    @staticmethod
    def _result(resp: httpx.Response, full_url: str) -> Any:
        if resp.status_code >= 400:
            try:
                err = resp.json().get("message", resp.text)
            except Exception:
                err = resp.text
            raise OvhApiError(resp.status_code, err, full_url)

        if resp.status_code == 204:
            return None
        return resp.json()

    async def get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, data: dict | None = None) -> Any:
        return await self._request("POST", path, data=data)

    async def delete(self, path: str) -> Any:
        return await self._request("DELETE", path)

    # ── Public (unauthenticated) helper ────────────────────────

    async def get_public(self, url: str, params: dict | None = None) -> Any:
        """GET without authentication (for public endpoints)."""
        resp = await self._http.get(url, params=params)
        if resp.status_code >= 400:
            raise OvhApiError(resp.status_code, resp.text, url)
        return resp.json()

