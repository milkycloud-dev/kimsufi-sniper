from __future__ import annotations

import html
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from config import EU_DATACENTERS, INTERVAL_CHOICES, PRICE_CHOICES, Config
from state import NOTIFY_KEYS, NOTIFY_LABELS, State

MODE_TEXT = {
    "notify": "Notify only",
    "reserve": "Auto-reserve (order created, paid by you via link)",
    "buy": "Auto-buy (paid with the default payment method)",
}


@dataclass
class Runtime:
    started: float = field(default_factory=time.time)
    checks: int = 0
    errors: int = 0
    error_streak: int = 0
    last_check: float | None = None
    last_error: str | None = None
    backoff: float = 0.0
    stock: dict[str, str] = field(default_factory=dict)
    last_stock_seen: float | None = None
    ovh_status: str | None = None


def fmt_time(ts: float | None) -> str:
    if not ts:
        return "never"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def per_minute(interval: int, plans: int) -> int:
    return round(60 / interval * max(1, plans))


def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def _mark(on: bool) -> str:
    return "✅" if on else "▫️"


class Panel:
    def __init__(
        self,
        config: Config,
        state: State,
        runtime: Runtime,
        on_change: Callable[[], None],
        on_check: Callable[[], Awaitable[str]],
    ) -> None:
        self.config = config
        self.state = state
        self.runtime = runtime
        self._on_change = on_change
        self._on_check = on_check

    def render(self, view: str = "main") -> tuple[str, dict]:
        builder = {
            "main": self._main,
            "interval": self._interval,
            "triggers": self._triggers,
            "dcs": self._dcs,
            "notify": self._notify,
            "buy": self._buy_confirm,
        }.get(view, self._main)
        text, rows = builder()
        return text, {"inline_keyboard": rows}

    def _plans_text(self) -> str:
        names = [self.config.plans[c] for c in self.state.active_plans()]
        return ", ".join(names) or "none selected"

    def _dcs_text(self) -> str:
        dcs = self.state.data["datacenters"]
        return ", ".join(dc.upper() for dc in dcs) if dcs else "any EU"

    def _lock_text(self) -> str:
        now = time.time()
        locks = {k: v for k, v in self.state.data["locks"].items() if v > now}
        if not locks:
            return "none"
        parts = []
        for key, until in sorted(locks.items()):
            name = "all plans" if key == "*" else self.config.plans.get(key, key)
            parts.append(f"{name} until {fmt_time(until)}")
        return "; ".join(parts)

    def _main(self) -> tuple[str, list]:
        d = self.state.data
        rt = self.runtime
        plans = len(self.state.active_plans())
        effective_mode = d["mode"]
        warn = ""
        if effective_mode != "notify" and rt.ovh_status is None and not self.config.has_ovh_credentials:
            warn = "\n⚠️ OVH API keys are not set: the bot only notifies."
        elif effective_mode != "notify" and rt.ovh_status and rt.ovh_status != "ok":
            warn = f"\n⚠️ OVH API keys: {html.escape(rt.ovh_status)}"
        if effective_mode == "buy" and not self.config.allow_autopay:
            warn += "\n⚠️ Auto-buy is disabled on this deployment: reservations are made instead."
        if rt.stock:
            stock = ", ".join(
                f"{html.escape(self.config.plans.get(k.split('@')[0], k))} {html.escape(k.split('@')[1].upper())} ({html.escape(v)})"
                for k, v in sorted(rt.stock.items())
            )
        else:
            stock = "none"
        health = "ok"
        if rt.error_streak:
            health = f"{rt.error_streak} failed in a row, backoff {rt.backoff:.0f} s: {html.escape(rt.last_error or '')}"
        text = (
            "<b>Kimsufi sniper panel</b>\n\n"
            f"State: <b>{'running' if d['enabled'] else 'paused'}</b>\n"
            f"Mode: <b>{MODE_TEXT[d['mode']]}</b>\n"
            f"Check every <b>{d['interval']} s</b> ({per_minute(d['interval'], plans)} requests/min)\n"
            f"Plans: <b>{html.escape(self._plans_text())}</b>\n"
            f"Datacenters: <b>{self._dcs_text()}</b>\n"
            f"Trigger: <b>{'1H delivery only' if d['speed'] == 'fast' else 'any delivery time'}</b>, "
            f"price limit <b>{d['max_price']:.2f} {self.config.currency}</b> excl. tax\n"
            f"One server only: <b>{'on' if d['one_server'] else 'off'}</b>\n"
            f"Reservation lock: <b>{self._lock_text()}</b>\n"
            f"{warn}\n"
            f"Stock now: <b>{stock}</b>\n"
            f"Last stock seen: {fmt_time(rt.last_stock_seen)}\n"
            f"Last check: {fmt_time(rt.last_check)}, API: {health}\n"
            f"Checks: {rt.checks}, errors: {rt.errors}, up since {fmt_time(rt.started)}\n"
            f"\n<i>Updated {fmt_time(time.time())}</i>"
        )
        rows = [
            [_btn("⏸ Pause" if d["enabled"] else "▶️ Start", "t:enabled")],
            [
                _btn(("• " if d["mode"] == "notify" else "") + "Notify", "m:notify"),
                _btn(("• " if d["mode"] == "reserve" else "") + "Reserve", "m:reserve"),
                _btn(("• " if d["mode"] == "buy" else "") + "Buy" + ("" if self.config.allow_autopay else " 🔒"), "m:buy"),
            ],
            [_btn(f"⏱ Interval: {d['interval']} s", "v:interval"), _btn("🎯 Triggers", "v:triggers")],
            [_btn("🔔 Notifications", "v:notify"), _btn("🔎 Check now", "chk")],
        ]
        if self._lock_text() != "none":
            rows.append([_btn("🔓 Clear reservation lock", "unlock")])
        rows.append([_btn("↻ Refresh", "v:main")])
        return text, rows

    def _interval(self) -> tuple[str, list]:
        d = self.state.data
        plans = len(self.state.active_plans())
        text = (
            "<b>Check interval</b>\n\n"
            f"Current: <b>{d['interval']} s</b>. One request per selected plan per check "
            f"({plans} now), about 216 bytes each.\n\n"
            "Measured against the OVH availability API: 4 requests/s for 30 s "
            "from this server passed with no HTTP 429 and no rate-limit headers. "
            "OVH publishes no limit for this endpoint, and its data changes on a scale of minutes, "
            "so 5 s is the recommended value and 2 s the floor. "
            "On HTTP 429 or 5xx the bot backs off by itself (up to 5 min)."
        )
        row = []
        rows = []
        for value in INTERVAL_CHOICES:
            label = f"{'• ' if value == d['interval'] else ''}{value} s ({per_minute(value, plans)}/min)"
            row.append(_btn(label, f"i:{value}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([_btn("← Back", "v:main")])
        return text, rows

    def _triggers(self) -> tuple[str, list]:
        d = self.state.data
        text = (
            "<b>Triggers</b>\n\n"
            "An alert or reservation fires when a selected plan turns in stock "
            "in a selected datacenter with an allowed delivery time and a price under the limit.\n\n"
            f"Datacenters: <b>{self._dcs_text()}</b>\n"
            f"Price limit: <b>{d['max_price']:.2f} {self.config.currency}</b> excl. tax (checked in the cart before ordering)\n"
            "One server only: after one successful reservation all plans are locked "
            f"for {self.config.reserve_cooldown_hours} h; off locks only the reserved plan."
        )
        rows = [
            [_btn(f"{_mark(d['plans'].get(code, False))} {label} ({code})", f"p:{code}")]
            for code, label in self.config.plans.items()
        ]
        rows.append([_btn(f"📍 Datacenters: {self._dcs_text()}", "v:dcs")])
        rows.append([_btn(
            "⚡ Delivery: 1H only" if d["speed"] == "fast" else "⚡ Delivery: any (1H to 72H)", "sp"
        )])
        rows.append([_btn(f"💶 Price limit: {d['max_price']:.2f} {self.config.currency}", "pr")])
        rows.append([_btn(f"{_mark(d['one_server'])} One server only", "t:one_server")])
        rows.append([_btn("← Back", "v:main")])
        return text, rows

    def _dcs(self) -> tuple[str, list]:
        selected = set(self.state.data["datacenters"])
        text = (
            "<b>Datacenters</b>\n\n"
            "Nothing selected means any EU datacenter. "
            "KS-B and KS-C are currently listed only in GRA."
        )
        rows = [[_btn(f"{_mark(not selected)} Any EU", "dc:*")]]
        row = []
        for dc in EU_DATACENTERS:
            row.append(_btn(f"{'✅ ' if dc in selected else ''}{dc.upper()}", f"dc:{dc}"))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([_btn("← Back", "v:triggers")])
        return text, rows

    def _notify(self) -> tuple[str, list]:
        text = "<b>Notifications</b>\n\nAll are on by default. Messages go only to the owner."
        rows = [
            [_btn(f"{_mark(self.state.notify_on(key))} {NOTIFY_LABELS[key]}", f"n:{key}")]
            for key in NOTIFY_KEYS
        ]
        rows.append([_btn("← Back", "v:main")])
        return text, rows

    def _buy_confirm(self) -> tuple[str, list]:
        text = (
            "<b>Switch to auto-buy?</b>\n\n"
            "The order will be paid at once with the default payment method on the OVH account. "
            "Reserve mode creates the same order but leaves the payment to you."
        )
        rows = [[_btn("Yes, auto-buy", "m:buy!"), _btn("Cancel", "v:main")]]
        return text, rows

    async def handle(self, data: str) -> tuple[str, str | None]:
        d = self.state.data
        kind, _, arg = data.partition(":")

        if kind == "v":
            return arg or "main", None

        if kind == "chk":
            return "main", await self._on_check()

        if kind == "t" and arg in ("enabled", "one_server"):
            d[arg] = not d[arg]
            return self._commit("triggers" if arg == "one_server" else "main")

        if kind == "m":
            if arg == "buy" or arg == "buy!":
                if not self.config.allow_autopay:
                    return "main", "Auto-buy is disabled here (ALLOW_AUTOPAY=false)"
                if arg == "buy":
                    return "buy", None
                d["mode"] = "buy"
                return self._commit("main", "Mode: auto-buy")
            if arg in ("notify", "reserve"):
                d["mode"] = arg
                return self._commit("main", f"Mode: {arg}")

        if kind == "i" and arg.isdigit() and int(arg) in INTERVAL_CHOICES:
            d["interval"] = int(arg)
            return self._commit("interval", f"Interval: {arg} s")

        if kind == "p" and arg in self.config.plans:
            d["plans"][arg] = not d["plans"].get(arg, False)
            return self._commit("triggers")

        if kind == "dc":
            if arg == "*":
                d["datacenters"] = []
            elif arg in EU_DATACENTERS:
                dcs = set(d["datacenters"])
                dcs.symmetric_difference_update({arg})
                d["datacenters"] = [dc for dc in EU_DATACENTERS if dc in dcs]
            return self._commit("dcs")

        if kind == "sp":
            d["speed"] = "any" if d["speed"] == "fast" else "fast"
            return self._commit("triggers")

        if kind == "pr":
            choices = list(PRICE_CHOICES)
            current = float(d["max_price"])
            nxt = next((p for p in choices if p > current + 1e-9), choices[0])
            d["max_price"] = nxt
            return self._commit("triggers", f"Price limit: {nxt:.2f} {self.config.currency}")

        if kind == "n" and arg in NOTIFY_KEYS:
            d["notify"][arg] = not d["notify"].get(arg, True)
            return self._commit("notify")

        if kind == "unlock":
            self.state.clear_locks()
            return self._commit("main", "Lock cleared")

        return "main", None

    def _commit(self, view: str, toast: str | None = None) -> tuple[str, str | None]:
        self.state.save()
        self._on_change()
        return view, toast
