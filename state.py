from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from config import INTERVAL_CHOICES, MODES, Config

logger = logging.getLogger(__name__)

NOTIFY_KEYS = ("available", "sold_out", "orders", "errors", "startup", "daily")

NOTIFY_LABELS = {
    "available": "In stock",
    "sold_out": "Sold out again",
    "orders": "Reservations and orders",
    "errors": "API errors",
    "startup": "Start and stop",
    "daily": "Daily summary",
}

SPEEDS = ("any", "fast")


def defaults(config: Config) -> dict[str, Any]:
    return {
        "enabled": True,
        "mode": config.default_mode,
        "interval": config.default_interval,
        "plans": {code: True for code in config.plans},
        "datacenters": [],
        "speed": "any",
        "max_price": config.default_max_price,
        "one_server": True,
        "notify": {key: True for key in NOTIFY_KEYS},
        "locks": {},
        "orders": [],
        "owner_id": None,
        "panel": None,
    }


class State:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._path = Path(config.state_path)
        self.data = defaults(config)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            saved = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.error("State file unreadable, using defaults: %s", exc)
            return
        for key, value in saved.items():
            if key not in self.data:
                continue
            if isinstance(self.data[key], dict) and isinstance(value, dict) and key in ("plans", "notify"):
                for sub, flag in value.items():
                    if sub in self.data[key]:
                        self.data[key][sub] = bool(flag)
            else:
                self.data[key] = value
        self._sanitize()

    def _sanitize(self) -> None:
        d = self.data
        if d["mode"] not in MODES:
            d["mode"] = self._config.default_mode
        if d["interval"] not in INTERVAL_CHOICES:
            d["interval"] = min(INTERVAL_CHOICES, key=lambda v: abs(v - int(d["interval"] or 5)))
        if d["speed"] not in SPEEDS:
            d["speed"] = "any"
        d["datacenters"] = [str(dc).lower() for dc in d["datacenters"] or []]
        d["locks"] = {k: float(v) for k, v in (d["locks"] or {}).items()}
        d["orders"] = list(d["orders"] or [])[-20:]

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".state-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)

    @property
    def enabled(self) -> bool:
        return bool(self.data["enabled"])

    @property
    def mode(self) -> str:
        return self.data["mode"]

    @property
    def interval(self) -> int:
        return int(self.data["interval"])

    def active_plans(self) -> list[str]:
        return [code for code in self._config.plans if self.data["plans"].get(code)]

    def notify_on(self, key: str) -> bool:
        return bool(self.data["notify"].get(key, True))

    def lock_until(self, plan_code: str, now: float | None = None) -> float | None:
        now = time.time() if now is None else now
        keys = ["*", plan_code]
        until = max((self.data["locks"].get(k, 0.0) for k in keys), default=0.0)
        return until if until > now else None

    def add_lock(self, plan_code: str, hours: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        key = "*" if self.data["one_server"] else plan_code
        self.data["locks"][key] = now + hours * 3600
        self.data["locks"] = {k: v for k, v in self.data["locks"].items() if v > now}

    def clear_locks(self) -> None:
        self.data["locks"] = {}

    def record_order(self, entry: dict[str, Any]) -> None:
        self.data["orders"].append(entry)
        self.data["orders"] = self.data["orders"][-20:]
