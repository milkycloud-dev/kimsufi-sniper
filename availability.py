from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import httpx

from config import EU_DATACENTERS, EXCLUDED_REGIONS

logger = logging.getLogger(__name__)

AVAILABILITY_URL = "https://eu.api.ovh.com/v1/dedicated/server/datacenter/availabilities"

_IN_STOCK = re.compile(r"\d+h(-low|-high)?")


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None) -> None:
        self.status = status
        self.retry_after = retry_after
        super().__init__(message)


def in_stock(status: str) -> bool:
    s = (status or "").strip().lower()
    return s == "available" or bool(_IN_STOCK.fullmatch(s))


def is_fast(status: str) -> bool:
    s = (status or "").strip().lower()
    return s == "available" or s.startswith("1h")


@dataclass(frozen=True)
class ServerAvailability:
    plan_code: str
    fqn: str
    datacenter: str
    availability: str

    @property
    def key(self) -> str:
        return f"{self.plan_code}@{self.datacenter}"

    @property
    def label(self) -> str:
        return f"{self.plan_code} @ {self.datacenter} ({self.availability})"


def is_eu(dc: str) -> bool:
    return dc in EU_DATACENTERS and dc not in EXCLUDED_REGIONS


def parse(data: list[dict]) -> list[ServerAvailability]:
    results: list[ServerAvailability] = []
    for entry in data:
        plan_code = str(entry.get("planCode", "")).lower()
        fqn = str(entry.get("fqn", ""))
        for dc_info in entry.get("datacenters", []) or []:
            dc = str(dc_info.get("datacenter", "")).lower()
            if not is_eu(dc):
                continue
            results.append(ServerAvailability(plan_code, fqn, dc, str(dc_info.get("availability", ""))))
    return results


async def fetch_plan(http: httpx.AsyncClient, plan_code: str) -> list[ServerAvailability]:
    try:
        resp = await http.get(AVAILABILITY_URL, params={"planCode": plan_code})
    except httpx.HTTPError as exc:
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc
    if resp.status_code == 429 or resp.status_code >= 500:
        retry = resp.headers.get("retry-after", "")
        raise FetchError(
            f"HTTP {resp.status_code}",
            status=resp.status_code,
            retry_after=float(retry) if retry.replace(".", "", 1).isdigit() else None,
        )
    if resp.status_code != 200:
        raise FetchError(f"HTTP {resp.status_code}", status=resp.status_code)
    try:
        data = resp.json()
    except ValueError as exc:
        raise FetchError("invalid JSON") from exc
    if not isinstance(data, list):
        raise FetchError("unexpected payload")
    return [s for s in parse(data) if s.plan_code == plan_code.lower()]


async def fetch_all(http: httpx.AsyncClient, plan_codes: list[str]) -> list[ServerAvailability]:
    if not plan_codes:
        return []
    parts = await asyncio.gather(*(fetch_plan(http, code) for code in plan_codes))
    return [item for part in parts for item in part]


def select(
    entries: list[ServerAvailability],
    datacenters: list[str],
    speed: str,
) -> list[ServerAvailability]:
    wanted = set(datacenters)
    out = []
    for s in entries:
        if not in_stock(s.availability):
            continue
        if wanted and s.datacenter not in wanted:
            continue
        if speed == "fast" and not is_fast(s.availability):
            continue
        out.append(s)
    return out
