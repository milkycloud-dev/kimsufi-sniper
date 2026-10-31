from __future__ import annotations

import asyncio
import html
import re
import logging
import os
import signal
import sys
import time

import httpx

from availability import FetchError, ServerAvailability, fetch_all, matches_location, select
from config import MAX_BACKOFF, Config, load_config
from order import DryRunResult, OrderResult, dry_run, place_order
from ovh_api import OvhApiError, OvhClient
from panel import Panel, Runtime, fmt_time
from state import State
from telegram_bot import OwnerGate, TelegramApi, TelegramError

logger = logging.getLogger("sniper")

MANUAL_URL = "https://eco.ovhcloud.com/en-ie/?display=list"
RETRY_SECONDS = 60
MAX_ATTEMPTS = 3
ERROR_ALERT_STREAK = 3
DAILY_SECONDS = 24 * 3600


class Sniper:
    def __init__(
        self,
        config: Config,
        http: httpx.AsyncClient | None = None,
        tg: TelegramApi | None = None,
        ovh: OvhClient | None = None,
    ) -> None:
        self.config = config
        self.state = State(config)
        self.runtime = Runtime()
        self.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            headers={"User-Agent": "kimsufi-sniper"},
        )
        self.tg = tg or TelegramApi(config.telegram_bot_token)
        self.ovh = ovh if ovh is not None else (OvhClient(config) if config.has_ovh_credentials else None)
        self.gate = OwnerGate(config.owner_id, config.owner_username)
        self.panel = Panel(
            config, self.state, self.runtime, self.wake, self.check_now,
            on_catalog=self.load_catalog, on_dry_run=self.start_dry_run,
        )
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._check_lock = asyncio.Lock()
        self._prev: dict[str, ServerAvailability] | None = None
        self._attempts: dict[str, tuple[int, float]] = {}
        self._error_alerted = False

    @property
    def owner_chat(self) -> int | None:
        return self.config.owner_id or self.state.data.get("owner_id")

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def _sleep(self, seconds: float) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            pass

    async def notify(self, key: str, text: str) -> None:
        chat = self.owner_chat
        if chat is None or not self.state.notify_on(key):
            return
        await self.tg.send(chat, text)

    async def run(self) -> None:
        if not self.config.telegram_bot_token:
            raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
        if self.owner_chat is None and not self.config.owner_username:
            raise SystemExit("Set OWNER_ID or OWNER_USERNAME")
        logger.info(
            "Starting: mode=%s interval=%ss plans=%s ovh_keys=%s autopay_allowed=%s",
            self.state.mode, self.state.interval, ",".join(self.state.active_plans()),
            self.config.has_ovh_credentials, self.config.allow_autopay,
        )
        await self._setup_commands()
        await self.check_ovh()
        await self.notify("startup", self._startup_text())
        tasks = [
            asyncio.create_task(self._poll_loop(), name="poll"),
            asyncio.create_task(self._updates_loop(), name="updates"),
            asyncio.create_task(self._daily_loop(), name="daily"),
        ]
        await self._stop.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.notify("startup", "🛑 <b>Kimsufi sniper stopped.</b>")
        await self.close()

    async def close(self) -> None:
        await self.http.aclose()
        await self.tg.close()
        if self.ovh:
            await self.ovh.close()

    async def check_ovh(self) -> str | None:
        if not self.ovh:
            self.runtime.ovh_status = None
            return None
        try:
            await self.ovh.get("/order/cart")
            status = "ok"
        except OvhApiError as exc:
            status = f"cannot use carts ({exc.status}: {exc.args[0].split(': ', 1)[-1].split(' [')[0]})"
        except Exception as exc:
            status = f"check failed ({type(exc).__name__})"
        changed = status != self.runtime.ovh_status
        self.runtime.ovh_status = status
        if status != "ok":
            logger.warning("OVH keys: %s", status)
            if changed and self.state.mode != "notify":
                await self.notify("errors", f"⚠️ <b>OVH API keys</b>: {html.escape(status)}. Reservations will fail until the key is fixed.")
        return status

    def _startup_text(self) -> str:
        names = ", ".join(self.state.label(c) for c in self.state.active_plans()) or "none"
        if not self.config.has_ovh_credentials:
            keys = "missing, notify only"
        else:
            keys = self.runtime.ovh_status or "set"
        return (
            "🤖 <b>Kimsufi sniper started</b>\n"
            f"Mode: {self.state.mode}, every {self.state.interval} s, plans: {html.escape(names)}\n"
            f"State: {'running' if self.state.enabled else 'paused'}, OVH keys: {keys}\n"
            "Open the panel with /start"
        )

    async def _setup_commands(self) -> None:
        try:
            await self.tg.call("deleteMyCommands")
            if self.owner_chat is not None:
                await self.tg.call(
                    "setMyCommands",
                    commands=[{"command": "start", "description": "Open the control panel"}],
                    scope={"type": "chat", "chat_id": self.owner_chat},
                )
        except TelegramError as exc:
            logger.warning("Commands not set: %s", exc)

    async def _poll_loop(self) -> None:
        while not self._stop.is_set():
            if not self.state.enabled or not self.state.active_plans():
                await self._sleep(60)
                continue
            try:
                await self.check_once()
            except FetchError as exc:
                await self._on_fetch_error(exc)
                await self._sleep(self.runtime.backoff)
                continue
            except Exception:
                logger.exception("Poll iteration failed")
            await self._sleep(self.state.interval)

    async def check_once(self) -> dict[str, ServerAvailability]:
        async with self._check_lock:
            entries = await fetch_all(self.http, self.state.active_plans())
            rt = self.runtime
            rt.seen_dcs.update(s.datacenter for s in entries)
            rt.checks += 1
            rt.last_check = time.time()
            if rt.error_streak:
                logger.info("Availability API recovered after %d errors", rt.error_streak)
                if self._error_alerted:
                    await self.notify("errors", "✅ <b>OVH availability API answers again.</b>")
                rt.error_streak = 0
                rt.backoff = 0.0
                self._error_alerted = False
            current = {
                s.key: s
                for s in select(entries, self.state.data["datacenters"], self.state.data["speed"], self.state.region)
            }
            await self._process(current)
            if rt.checks % 720 == 0:
                logger.info("Heartbeat: %d checks, %d errors", rt.checks, rt.errors)
            return current

    async def check_now(self) -> str:
        if not self.state.active_plans():
            return "No plans selected"
        if self.ovh and self.runtime.ovh_status != "ok":
            await self.check_ovh()
        try:
            current = await self.check_once()
        except FetchError as exc:
            await self._on_fetch_error(exc)
            return f"API error: {exc}"
        if not current:
            return "Checked: no stock"
        return "In stock: " + ", ".join(sorted(current))

    async def _on_fetch_error(self, exc: FetchError) -> None:
        rt = self.runtime
        rt.errors += 1
        rt.error_streak += 1
        rt.last_error = str(exc)
        base = max(float(self.state.interval), exc.retry_after or 0.0)
        rt.backoff = min(float(MAX_BACKOFF), max(base, rt.backoff * 2 if rt.backoff else base * 2))
        logger.warning("Availability API error %s (streak %d), next try in %.0f s", exc, rt.error_streak, rt.backoff)
        if rt.error_streak >= ERROR_ALERT_STREAK and not self._error_alerted:
            self._error_alerted = True
            await self.notify(
                "errors",
                f"⚠️ <b>OVH availability API fails</b>: {html.escape(str(exc))}\n"
                f"{rt.error_streak} errors in a row, backing off to {rt.backoff:.0f} s.",
            )

    def _name(self, s: ServerAvailability) -> str:
        return f"{self.state.label(s.plan_code)} ({s.plan_code}) {s.datacenter.upper()}"

    async def _process(self, current: dict[str, ServerAvailability]) -> None:
        prev = self._prev if self._prev is not None else {}
        new = [s for k, s in current.items() if k not in prev]
        gone = [s for k, s in prev.items() if k not in current]
        self._prev = current
        self.runtime.stock = {k: s.availability for k, s in current.items()}
        if current:
            self.runtime.last_stock_seen = time.time()
        for s in gone:
            self._attempts.pop(s.key, None)

        if new:
            logger.info("In stock: %s", ", ".join(s.label for s in new))
            await self.notify("available", self._available_text(new))
        if gone:
            logger.info("Sold out: %s", ", ".join(s.label for s in gone))
            await self.notify(
                "sold_out",
                "🔴 <b>Sold out again</b>\n" + "\n".join(html.escape(self._name(s)) for s in gone),
            )
        if current and self.state.mode != "notify":
            await self._order(list(current.values()))

    def _action_text(self, servers: list[ServerAvailability]) -> str:
        mode = self.state.mode
        if mode == "notify":
            return "Mode: notify only."
        if not self.ovh:
            return "OVH API keys are not set, so nothing is reserved."
        locks = [self.state.lock_until(s.plan_code) for s in servers]
        if all(locks):
            return f"Reservation lock is active until {fmt_time(min(locks))}, nothing is reserved."
        if mode == "buy" and self.config.allow_autopay:
            return "⚡ Buying now..."
        return "⏳ Reserving now..."

    def _available_text(self, servers: list[ServerAvailability]) -> str:
        lines = ["🟢 <b>Kimsufi in stock</b>"]
        for s in servers:
            lines.append(f"• {html.escape(self._name(s))}: <code>{html.escape(s.availability)}</code>")
        lines.append("")
        lines.append(self._action_text(servers))
        lines.append(f'<a href="{MANUAL_URL}">Order manually</a>')
        return "\n".join(lines)

    def _candidates(self, servers: list[ServerAvailability], now: float) -> list[ServerAvailability]:
        order = list(self.state.all_plans())
        out = []
        for s in sorted(servers, key=lambda x: (order.index(x.plan_code) if x.plan_code in order else 99, x.datacenter)):
            if self.state.lock_until(s.plan_code, now):
                continue
            count, last = self._attempts.get(s.key, (0, 0.0))
            if count >= MAX_ATTEMPTS or (count and now - last < RETRY_SECONDS):
                continue
            out.append(s)
        return out

    async def _order(self, servers: list[ServerAvailability]) -> None:
        if not self.ovh:
            return
        autopay = self.state.mode == "buy" and self.config.allow_autopay
        for s in self._candidates(servers, time.time()):
            now = time.time()
            if self.state.lock_until(s.plan_code, now):
                continue
            count, _ = self._attempts.get(s.key, (0, 0.0))
            self._attempts[s.key] = (count + 1, now)
            result = await place_order(self.ovh, self.config, s, float(self.state.data["max_price"]), autopay)
            self.state.record_order({
                "ts": now,
                "plan": s.plan_code,
                "dc": s.datacenter,
                "autopay": autopay,
                "success": result.success,
                "order_id": result.order_id,
                "error": result.error,
            })
            if result.success:
                self.state.add_lock(s.plan_code, self.config.reserve_cooldown_hours, now)
            self.state.save()
            await self.notify("orders", self._order_text(s, result, count + 1))
            if result.success and self.state.data["one_server"]:
                break

    def _order_text(self, s: ServerAvailability, r: OrderResult, attempt: int) -> str:
        kind = "Order" if r.autopay else "Reservation"
        if r.success:
            price = ""
            if r.price is not None:
                cur = self.config.currency
                price = f"\nPrice: {r.price:.2f} {cur} excl. tax, {r.price_with_tax or 0:.2f} {cur} incl. tax"
            pay = "Paid with the default payment method." if r.autopay else "Pay it to get the server: OVH delivers only after payment."
            link = f'\n<a href="{html.escape(r.order_url)}">Open the order</a>' if r.order_url else ""
            lock = self.state.lock_until(s.plan_code)
            return (
                f"🧾 <b>{kind} created</b>\n"
                f"{html.escape(self._name(s))}\n"
                f"Order: <code>{html.escape(r.order_id or '?')}</code>{price}\n"
                f"{pay}{link}\n"
                f"New reservations paused until {fmt_time(lock)}."
            )
        retry = "Will retry in 1 min." if attempt < MAX_ATTEMPTS else "No more retries while this stock lasts."
        return (
            f"❌ <b>{kind} failed</b> (attempt {attempt}/{MAX_ATTEMPTS})\n"
            f"{html.escape(self._name(s))}\n"
            f"{html.escape(r.error or 'unknown error')}\n"
            f"{retry}\n"
            f'<a href="{MANUAL_URL}">Order manually</a>'
        )

    async def load_catalog(self) -> None:
        rt = self.runtime
        try:
            resp = await self.http.get(
                f"{self.config.ovh_api_base}/order/catalog/public/eco",
                params={"ovhSubsidiary": self.config.ovh_subsidiary},
            )
            resp.raise_for_status()
            plans = resp.json().get("plans", [])
        except (httpx.HTTPError, ValueError) as exc:
            rt.catalog_error = f"{type(exc).__name__}: {exc}"[:200]
            logger.warning("Catalog load failed: %s", rt.catalog_error)
            return
        items = []
        for plan in plans:
            code = str(plan.get("planCode", "")).lower()
            name = str(plan.get("invoiceName", ""))
            if "sk" not in code or not name.upper().startswith("KS"):
                continue
            label = name.split("|")[0].strip()
            suffix = re.search(r"-(sgp|syd|ynm|bhs)$", code)
            if suffix:
                label += f" {suffix.group(1).upper()}"
            price = None
            for pricing in plan.get("pricings", []):
                if pricing.get("interval") == 1 and pricing.get("mode") == "default" and "renew" in pricing.get("capacities", []):
                    price = pricing.get("price", 0) / 1e8
                    break
            items.append((code, label, price))
        items.sort(key=lambda x: (x[2] if x[2] is not None else 1e9, x[1]))
        rt.catalog = items
        rt.catalog_error = None

    async def dry_run_targets(self, plans: list[str]) -> tuple[list[ServerAvailability], list[str]]:
        codes = list(dict.fromkeys(p.split("@", 1)[0] for p in plans))
        entries = await fetch_all(self.http, codes)
        self.runtime.seen_dcs.update(s.datacenter for s in entries)
        targets: list[ServerAvailability] = []
        notes: list[str] = []
        for code in plans:
            want_dc = None
            if "@" in code:
                code, want_dc = code.split("@", 1)
            rows = [s for s in entries if s.plan_code == code]
            if want_dc:
                pick = next((s for s in rows if s.datacenter == want_dc), None)
                if pick is None:
                    fqn = rows[0].fqn if rows else code
                    pick = ServerAvailability(code, fqn, want_dc, "dry-run")
            else:
                pick = next((s for s in rows if matches_location(
                    s.datacenter, self.state.data["datacenters"], self.state.region)), None)
            if pick is None:
                listed = ", ".join(sorted({s.datacenter.upper() for s in rows})) or "none"
                notes.append(f"{self.state.label(code)} ({code}): no datacenter matches the filters (listed: {listed})")
                continue
            targets.append(pick)
        return targets, notes

    async def run_dry_run(self, plans: list[str] | None = None) -> tuple[list[DryRunResult], list[str]]:
        plans = plans or self.state.active_plans()
        targets, notes = await self.dry_run_targets([p.lower() for p in plans])
        authed = bool(self.ovh) and await self.check_ovh() == "ok"
        client = self.ovh if authed else OvhClient(self.config, anonymous=True)
        results = []
        try:
            for server in targets:
                results.append(await dry_run(client, self.config, server, float(self.state.data["max_price"]), authed))
        finally:
            if not authed:
                await client.close()
        return results, notes

    def dry_run_text(self, results: list[DryRunResult], notes: list[str]) -> str:
        ok = sum(1 for r in results if r.ok)
        lines = [f"🧪 <b>Dry run</b>: {ok}/{len(results)} passed, no order was placed"]
        for r in results:
            head = "✅" if r.ok else "❌"
            lines.append(f"\n{head} <b>{html.escape(self._name(r.server))}</b> ({html.escape(r.server.availability)})")
            for step in r.steps:
                lines.append(f"• {html.escape(step)}")
            if r.error and r.error != "price outside the limit":
                lines.append(f"• error: {html.escape(r.error)}")
        for note in notes:
            lines.append(f"\n⚠️ {html.escape(note)}")
        if results and not results[0].authenticated:
            lines.append("\nKeys cannot use carts, so the run used an anonymous cart: assign and checkout were skipped.")
        return "\n".join(lines)

    async def start_dry_run(self) -> str:
        chat = self.owner_chat

        async def job() -> None:
            try:
                results, notes = await self.run_dry_run()
                text = self.dry_run_text(results, notes)
            except FetchError as exc:
                text = f"🧪 <b>Dry run</b> failed: {html.escape(str(exc))}"
            if chat is not None:
                await self.tg.send(chat, text)

        if not self.state.active_plans():
            return "No plans selected"
        asyncio.create_task(job())
        return "Dry run started, report follows"

    async def _daily_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(DAILY_SECONDS)
            rt = self.runtime
            await self.notify(
                "daily",
                "📊 <b>Daily summary</b>\n"
                f"State: {'running' if self.state.enabled else 'paused'}, mode {self.state.mode}, every {self.state.interval} s\n"
                f"Checks: {rt.checks}, errors: {rt.errors}\n"
                f"Last stock seen: {fmt_time(rt.last_stock_seen)}",
            )

    async def _updates_loop(self) -> None:
        offset: int | None = None
        while not self._stop.is_set():
            try:
                updates = await self.tg.get_updates(offset)
            except TelegramError as exc:
                logger.warning("getUpdates failed: %s", exc)
                await asyncio.sleep(exc.retry_after or 5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    await self.handle_update(update)
                except Exception:
                    logger.exception("Update %s failed", update.get("update_id"))

    async def handle_update(self, update: dict) -> None:
        member = update.get("my_chat_member")
        if member:
            chat = member.get("chat") or {}
            status = (member.get("new_chat_member") or {}).get("status")
            if chat.get("type") != "private" and status in ("member", "administrator"):
                logger.info("Added to %s %s, leaving", chat.get("type"), chat.get("id"))
                await self.tg.leave(chat["id"])
            return

        allowed, pin = self.gate.resolve(update, self.state.data.get("owner_id"))
        if not allowed:
            chat = ((update.get("message") or {}).get("chat")) or {}
            if chat.get("type") in ("group", "supergroup", "channel"):
                await self.tg.leave(chat["id"])
            return

        if pin is not None:
            self.state.data["owner_id"] = pin
            self.state.save()
            logger.info("Owner pinned: %s", pin)
            await self._setup_commands()

        if "message" in update:
            text = (update["message"].get("text") or "").strip()
            if text.split("@")[0] in ("/start", "/panel"):
                await self._open_panel(update["message"]["chat"]["id"])
            return

        cq = update["callback_query"]
        msg = cq.get("message") or {}
        view, toast = await self.panel.handle(str(cq.get("data", "")))
        text, markup = self.panel.render(view)
        await self.tg.edit(msg["chat"]["id"], msg["message_id"], text, markup)
        await self.tg.answer(cq["id"], toast)

    async def _open_panel(self, chat_id: int) -> None:
        old = self.state.data.get("panel")
        text, markup = self.panel.render("main")
        sent = await self.tg.send(chat_id, text, markup)
        if sent:
            if old and old.get("message_id") != sent["message_id"]:
                await self.tg.delete(old["chat_id"], old["message_id"])
            self.state.data["panel"] = {"chat_id": chat_id, "message_id": sent["message_id"]}
            self.state.save()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(name)-12s | %(levelname)-5s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def dry_run_cli(config: Config, plans: list[str]) -> int:
    sniper = Sniper(config)
    try:
        results, notes = await sniper.run_dry_run(plans or None)
        text = sniper.dry_run_text(results, notes)
        print(html.unescape(re.sub(r"<[^>]+>", "", text)))
        return 0 if results and all(r.ok for r in results) and not notes else 1
    finally:
        await sniper.close()


async def main() -> None:
    config = load_config()
    setup_logging(config.log_level)
    if len(sys.argv) > 1 and sys.argv[1] == "--dry-run":
        raise SystemExit(await dry_run_cli(config, sys.argv[2:]))
    if config.nice and hasattr(os, "nice"):
        os.nice(config.nice)
    sniper = Sniper(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, sniper.stop)
        except NotImplementedError:
            pass
    await sniper.run()


if __name__ == "__main__":
    asyncio.run(main())
