from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

import unittest
from dataclasses import replace
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from availability import FetchError, ServerAvailability, fetch_all, fetch_plan, in_stock, is_fast, parse, select
from config import Config, _plans, dc_region, load_config
from main import ERROR_ALERT_STREAK, MAX_ATTEMPTS, Sniper
from order import dry_run, pick_options, pick_region, place_order
from ovh_api import OvhApiError
from state import State
from telegram_bot import OwnerGate

OWNER = 111
STRANGER = 222
FQN_B = "25skb012.ram-32g-ecc-1333.noraid-1x120ssd"
FQN_C = "25skc012.ram-32g-ecc-1333.noraid-1x120ssd"


def _plan(code, name, price):
    return {"planCode": code, "invoiceName": name, "pricings": [
        {"interval": 1, "mode": "default", "capacities": ["installation", "renew"], "price": int(price * 1e8)},
    ]}


CATALOG = [
    _plan("24sk302-sgp", "KS-3 | Intel Xeon-E3 1245 v5", 21.1),
    _plan("25skb012", "KS-B | Intel Xeon E5-1620v2", 11.1),
    _plan("24sk302", "KS-3 | Intel Xeon-E3 1245 v5", 21.1),
    _plan("25skc012", "KS-C | Intel Xeon E5-1650v2", 13.3),
    _plan("26rise01", "RISE-1 | something", 50.0),
]


def feed(plan: str, fqn: str, dcs: dict[str, str]) -> list[dict]:
    return [{"planCode": plan, "fqn": fqn, "datacenters": [{"datacenter": k, "availability": v} for k, v in dcs.items()]}]


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, dict | None]] = []
        self.edits: list[tuple[int, int, str]] = []
        self.answers: list[tuple[str, str | None]] = []
        self.left: list[int] = []
        self.deleted: list[int] = []
        self.calls: list[tuple[str, dict]] = []
        self._next = 100

    async def call(self, method, **params):
        self.calls.append((method, params))
        return True

    async def send(self, chat_id, text, markup=None):
        self._next += 1
        self.sent.append((chat_id, text, markup))
        return {"message_id": self._next}

    async def edit(self, chat_id, message_id, text, markup=None):
        self.edits.append((chat_id, message_id, text))
        return True

    async def answer(self, callback_id, text=None):
        self.answers.append((callback_id, text))

    async def delete(self, chat_id, message_id):
        self.deleted.append(message_id)

    async def leave(self, chat_id):
        self.left.append(chat_id)

    async def close(self):
        pass


class FakeOvh:
    def __init__(self, price: float = 9.99, fail_on: str | None = None) -> None:
        self.price = price
        self.fail_on = fail_on
        self.log: list[tuple[str, str, object]] = []

    async def _hit(self, method, path, data=None):
        self.log.append((method, path, data))
        if self.fail_on and self.fail_on in path and method == "POST":
            raise OvhApiError(400, "boom", path)

    async def post(self, path, data=None):
        await self._hit("POST", path, data)
        if path == "/order/cart":
            return {"cartId": "c1"}
        if path.endswith("/eco"):
            return {"itemId": 7}
        if path.endswith("/checkout"):
            return {"orderId": 555, "url": "https://www.ovh.com/cgi-bin/order/display-order.cgi?orderId=555"}
        return {}

    async def get(self, path, params=None):
        await self._hit("GET", path, params)
        if path.endswith("/eco/options"):
            return [
                {"family": "memory", "mandatory": True, "planCode": "ram-32g-ecc-1333-25skb01"},
                {"family": "storage", "mandatory": True, "planCode": "softraid-2x480ssd-25skb01"},
                {"family": "storage", "mandatory": True, "planCode": "noraid-1x120ssd-25skb01"},
                {"family": "bandwidth", "mandatory": True, "planCode": "bandwidth-500-25sk"},
                {"family": "application-license", "mandatory": False, "planCode": "plesk-webadmin"},
            ]
        if path.endswith("/requiredConfiguration"):
            return [
                {"label": "dedicated_datacenter", "required": True, "allowedValues": ["gra"]},
                {"label": "dedicated_os", "required": True, "allowedValues": ["none_64.en"]},
                {"label": "region", "required": True, "allowedValues": ["europe"]},
                {"label": "enable-backup", "required": False, "allowedValues": ["false", "true"]},
            ]
        if path.endswith("/checkout"):
            return {"prices": {"withoutTax": {"value": self.price}, "withTax": {"value": round(self.price * 1.23, 2)}}}
        if "/item/" in path:
            return {"prices": [{"label": "TOTAL", "price": {"value": self.price}}]}
        return {}

    async def delete(self, path):
        self.log.append(("DELETE", path, None))

    async def close(self):
        pass

    def posted(self, suffix):
        return [d for m, p, d in self.log if m == "POST" and p.endswith(suffix)]


def make_config(tmp: str, **kw) -> Config:
    base = Config(
        telegram_bot_token="x",
        owner_id=None,
        owner_username="owneruser",
        state_path=os.path.join(tmp, "state.json"),
    )
    return replace(base, **kw)


def msg(user_id, text="/start", username="owneruser", chat_type="private", chat_id=None, is_bot=False, uid=1):
    return {"update_id": uid, "message": {
        "message_id": 1,
        "from": {"id": user_id, "username": username, "is_bot": is_bot},
        "chat": {"id": chat_id if chat_id is not None else user_id, "type": chat_type},
        "text": text,
    }}


def cbq(user_id, data, chat_id=None, uid=2):
    return {"update_id": uid, "callback_query": {
        "id": f"cb{uid}",
        "from": {"id": user_id, "username": "someone"},
        "message": {"message_id": 50, "chat": {"id": chat_id if chat_id is not None else user_id, "type": "private"}},
        "data": data,
    }}


class StatusTests(unittest.TestCase):
    def test_in_stock(self):
        for s in ("1H-low", "1H-high", "1h", "24H", "72H", "available", "480H"):
            self.assertTrue(in_stock(s), s)
        for s in ("unavailable", "unknown", "", "comingSoon", "H"):
            self.assertFalse(in_stock(s), s)

    def test_fast(self):
        self.assertTrue(is_fast("1H-low"))
        self.assertTrue(is_fast("available"))
        self.assertFalse(is_fast("72H"))

    def test_parse_keeps_all_and_region_filters(self):
        data = feed("24sk302", "24sk302.x", {
            "gra": "1H-low", "eu-west-par-a": "24H", "bhs": "1H-low", "ca-east-tor-a": "72H", "syd": "1H-low", "ynm": "1H",
        })
        entries = parse(data)
        self.assertEqual(len(entries), 6)
        self.assertEqual([s.datacenter for s in select(entries, [], "any")], ["gra", "eu-west-par-a"])
        self.assertEqual([s.datacenter for s in select(entries, [], "any", "na")], ["bhs", "ca-east-tor-a"])
        self.assertEqual([s.datacenter for s in select(entries, [], "any", "apac")], ["syd", "ynm"])
        self.assertEqual(len(select(entries, [], "any", "all")), 6)
        self.assertEqual([s.datacenter for s in select(entries, ["bhs", "syd"], "any", "eu")], ["bhs", "syd"])

    def test_dc_region(self):
        self.assertEqual(dc_region("gra"), "eu")
        self.assertEqual(dc_region("eu-west-par-c"), "eu")
        self.assertEqual(dc_region("BHS"), "na")
        self.assertEqual(dc_region("us-east-vin-a"), "na")
        self.assertEqual(dc_region("sgp"), "apac")
        self.assertEqual(dc_region("zzz"), "other")

    def test_pick_region(self):
        self.assertEqual(pick_region("gra", ["canada", "europe"]), "europe")
        self.assertEqual(pick_region("bhs", ["canada", "europe"]), "canada")
        self.assertEqual(pick_region("sgp", ["canada"]), "canada")
        self.assertEqual(pick_region("syd", ["europe", "asia"]), "asia")

    def test_select(self):
        entries = [
            ServerAvailability("25skb012", FQN_B, "gra", "1H-low"),
            ServerAvailability("25skb012", FQN_B, "rbx", "72H"),
            ServerAvailability("25skc012", FQN_C, "gra", "unavailable"),
        ]
        self.assertEqual(len(select(entries, [], "any")), 2)
        self.assertEqual([s.datacenter for s in select(entries, [], "fast")], ["gra"])
        self.assertEqual([s.datacenter for s in select(entries, ["rbx"], "any")], ["rbx"])

    def test_plans_env(self):
        self.assertEqual(_plans("25skb012:KS-B, 25SKC012"), {"25skb012": "KS-B", "25skc012": "25skc012"})


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_per_plan_query(self):
        seen = []

        def handler(request: httpx.Request):
            seen.append(request.url.params.get("planCode"))
            code = request.url.params["planCode"]
            return httpx.Response(200, json=feed(code, code + ".x", {"gra": "unavailable"}))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            out = await fetch_all(http, ["25skb012", "25skc012"])
        self.assertEqual(sorted(seen), ["25skb012", "25skc012"])
        self.assertEqual(len(out), 2)

    async def test_429(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(429, headers={"Retry-After": "30"}, text="slow down")
        )) as http:
            with self.assertRaises(FetchError) as ctx:
                await fetch_plan(http, "25skb012")
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(ctx.exception.retry_after, 30.0)

    async def test_bad_payload(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"a": 1}))) as http:
            with self.assertRaises(FetchError):
                await fetch_plan(http, "25skb012")

    async def test_network_error(self):
        def boom(request):
            raise httpx.ConnectError("down")

        async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
            with self.assertRaises(FetchError):
                await fetch_plan(http, "25skb012")


class GateTests(unittest.TestCase):
    def test_bootstrap_by_username_only_on_start(self):
        gate = OwnerGate(None, "owneruser")
        self.assertEqual(gate.resolve(msg(OWNER, "/start"), None), (True, OWNER))
        self.assertEqual(gate.resolve(msg(OWNER, "hello"), None), (False, None))
        self.assertEqual(gate.resolve(msg(STRANGER, "/start", username="other"), None), (False, None))
        self.assertEqual(gate.resolve(msg(OWNER, "/start", username="OwnerUser"), None), (True, OWNER))

    def test_pinned_owner_wins_over_username(self):
        gate = OwnerGate(None, "owneruser")
        self.assertEqual(gate.resolve(msg(STRANGER, "/start", username="owneruser"), OWNER), (False, None))
        self.assertEqual(gate.resolve(msg(OWNER, "/start", username="renamed"), OWNER), (True, None))

    def test_config_owner(self):
        gate = OwnerGate(OWNER, "")
        self.assertTrue(gate.resolve(msg(OWNER), None)[0])
        self.assertFalse(gate.resolve(msg(STRANGER), None)[0])
        self.assertFalse(gate.resolve(msg(STRANGER), STRANGER)[0])

    def test_groups_bots_and_foreign_callbacks(self):
        gate = OwnerGate(OWNER, "owneruser")
        self.assertFalse(gate.resolve(msg(OWNER, chat_type="group", chat_id=-100), None)[0])
        self.assertFalse(gate.resolve(msg(OWNER, chat_type="supergroup", chat_id=-100), None)[0])
        self.assertFalse(gate.resolve(msg(OWNER, is_bot=True), None)[0])
        self.assertFalse(gate.resolve(cbq(STRANGER, "t:enabled"), None)[0])
        self.assertFalse(gate.resolve(cbq(STRANGER, "t:enabled", chat_id=OWNER), None)[0])
        self.assertTrue(gate.resolve(cbq(OWNER, "t:enabled"), None)[0])
        self.assertFalse(gate.resolve({"update_id": 9, "edited_message": {}}, None)[0])


class StateTests(unittest.TestCase):
    def test_defaults_persist_and_sanitize(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(tmp)
            st = State(cfg)
            self.assertTrue(st.enabled)
            self.assertEqual(st.mode, "reserve")
            self.assertTrue(all(st.data["notify"].values()))
            self.assertEqual(st.active_plans(), ["25skb012", "25skc012"])
            st.data["interval"] = 7
            st.data["mode"] = "weird"
            st.data["plans"]["25skc012"] = False
            st.save()
            st2 = State(cfg)
            self.assertIn(st2.interval, (5, 10))
            self.assertEqual(st2.mode, "reserve")
            self.assertEqual(st2.active_plans(), ["25skb012"])

    def test_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(tmp)
            Path(cfg.state_path).write_text("{not json", encoding="utf-8")
            self.assertEqual(State(cfg).mode, "reserve")

    def test_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = State(make_config(tmp))
            now = 1000.0
            st.add_lock("25skb012", 24, now)
            self.assertTrue(st.lock_until("25skc012", now))
            st.clear_locks()
            st.data["one_server"] = False
            st.add_lock("25skb012", 24, now)
            self.assertTrue(st.lock_until("25skb012", now))
            self.assertIsNone(st.lock_until("25skc012", now))
            self.assertIsNone(st.lock_until("25skb012", now + 25 * 3600))


class OrderTests(unittest.IsolatedAsyncioTestCase):
    def test_pick_options(self):
        opts = [
            {"family": "memory", "mandatory": True, "planCode": "ram-32g-ecc-1333-25skb01"},
            {"family": "storage", "mandatory": True, "planCode": "softraid-2x480ssd-25skb01"},
            {"family": "storage", "mandatory": True, "planCode": "noraid-1x120ssd-25skb01"},
            {"family": "bandwidth", "mandatory": True, "planCode": "bandwidth-500-25sk"},
            {"family": "x", "mandatory": False, "planCode": "plesk"},
        ]
        self.assertEqual(
            sorted(pick_options(FQN_B, opts)),
            ["bandwidth-500-25sk", "noraid-1x120ssd-25skb01", "ram-32g-ecc-1333-25skb01"],
        )

    async def test_reservation_no_autopay(self):
        ovh = FakeOvh()
        s = ServerAvailability("25skb012", FQN_B, "gra", "1H-low")
        r = await place_order(ovh, Config(), s, 15.0, autopay=False)
        self.assertTrue(r.success)
        self.assertFalse(r.autopay)
        self.assertEqual(r.order_id, "555")
        checkout = ovh.posted("/checkout")[0]
        self.assertIs(checkout["autoPayWithPreferredPaymentMethod"], False)
        configs = {d["label"]: d["value"] for d in ovh.posted("/configuration")}
        self.assertEqual(configs, {"dedicated_datacenter": "gra", "dedicated_os": "none_64.en", "region": "europe"})
        self.assertEqual(len(ovh.posted("/eco/options")), 3)

    async def test_price_guard(self):
        ovh = FakeOvh(price=19.0)
        s = ServerAvailability("25skb012", FQN_B, "gra", "1H-low")
        r = await place_order(ovh, Config(), s, 15.0, autopay=False)
        self.assertFalse(r.success)
        self.assertIn("outside the limit", r.error)
        self.assertEqual(ovh.posted("/checkout"), [])
        self.assertIn(("DELETE", "/order/cart/c1", None), ovh.log)

    async def test_datacenter_not_offered(self):
        ovh = FakeOvh()
        s = ServerAvailability("25skb012", FQN_B, "bhs", "1H-low")
        r = await place_order(ovh, Config(), s, 15.0, autopay=False)
        self.assertFalse(r.success)
        self.assertIn("not offered", r.error)
        self.assertEqual(ovh.posted("/checkout"), [])
        self.assertIn(("DELETE", "/order/cart/c1", None), ovh.log)

    async def test_dry_run_never_checks_out(self):
        for authed in (True, False):
            ovh = FakeOvh()
            s = ServerAvailability("25skb012", FQN_B, "gra", "unavailable")
            r = await dry_run(ovh, Config(), s, 15.0, authenticated=authed)
            self.assertTrue(r.ok, r.steps)
            self.assertEqual(r.price, 9.99)
            self.assertEqual(ovh.posted("/checkout"), [])
            self.assertEqual(bool(ovh.posted("/assign")), authed)
            self.assertIn(("DELETE", "/order/cart/c1", None), ovh.log)
        r = await dry_run(FakeOvh(price=40.0), Config(), s, 15.0, authenticated=False)
        self.assertFalse(r.ok)
        self.assertIn("OUTSIDE", r.steps[-1])

    async def test_api_error_cleans_cart(self):
        ovh = FakeOvh(fail_on="/eco/options")
        s = ServerAvailability("25skb012", FQN_B, "gra", "1H-low")
        r = await place_order(ovh, Config(), s, 15.0, autopay=False)
        self.assertFalse(r.success)
        self.assertIn(("DELETE", "/order/cart/c1", None), ovh.log)


class SniperTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.feed = {"25skb012": {"gra": "unavailable"}, "25skc012": {"gra": "unavailable"}}
        self.status = 200

        def handler(request: httpx.Request):
            if self.status != 200:
                return httpx.Response(self.status)
            if request.url.path.endswith("/catalog/public/eco"):
                self.catalog_subsidiary = request.url.params.get("ovhSubsidiary")
                return httpx.Response(200, json={"plans": CATALOG})
            code = request.url.params["planCode"]
            fqn = {"25skb012": FQN_B, "25skc012": FQN_C}.get(code, code + ".ram.disk")
            return httpx.Response(200, json=feed(code, fqn, self.feed.get(code, {"gra": "unavailable"})))

        self.handler = handler

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, ovh=None, **kw) -> tuple[Sniper, FakeTelegram]:
        tg = FakeTelegram()
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        sn = Sniper(make_config(self._tmp.name, **kw), http=http, tg=tg, ovh=ovh)
        return sn, tg

    async def test_stranger_gets_nothing(self):
        sn, tg = self.make(owner_id=OWNER)
        await sn.handle_update(msg(STRANGER, "/start", username="owneruser"))
        await sn.handle_update(msg(STRANGER, "hi", username="x"))
        await sn.handle_update(cbq(STRANGER, "t:enabled"))
        await sn.handle_update(cbq(STRANGER, "m:notify", chat_id=OWNER))
        self.assertEqual((tg.sent, tg.edits, tg.answers), ([], [], []))
        self.assertTrue(sn.state.enabled)
        self.assertEqual(sn.state.mode, "reserve")

    async def test_group_is_left(self):
        sn, tg = self.make(owner_id=OWNER)
        await sn.handle_update(msg(OWNER, "/start", chat_type="supergroup", chat_id=-100))
        await sn.handle_update({"update_id": 5, "my_chat_member": {
            "chat": {"id": -200, "type": "group"}, "new_chat_member": {"status": "member"}}})
        self.assertEqual(tg.left, [-100, -200])
        self.assertEqual(tg.sent, [])

    async def test_bootstrap_pins_owner_and_panel(self):
        sn, tg = self.make()
        await sn.handle_update(msg(OWNER, "/start"))
        self.assertEqual(sn.state.data["owner_id"], OWNER)
        self.assertEqual(len(tg.sent), 1)
        self.assertIn("Kimsufi sniper panel", tg.sent[0][1])
        self.assertTrue(any(m == "setMyCommands" and p["scope"]["chat_id"] == OWNER for m, p in tg.calls))
        await sn.handle_update(msg(STRANGER, "/start", username="owneruser"))
        self.assertEqual(len(tg.sent), 1)
        await sn.handle_update(msg(OWNER, "/start", uid=3))
        self.assertEqual(len(tg.sent), 2)
        self.assertEqual(tg.deleted, [101])

    async def test_panel_callbacks(self):
        sn, tg = self.make(owner_id=OWNER)
        await sn.handle_update(cbq(OWNER, "t:enabled"))
        self.assertFalse(sn.state.enabled)
        await sn.handle_update(cbq(OWNER, "i:2"))
        self.assertEqual(sn.state.interval, 2)
        await sn.handle_update(cbq(OWNER, "i:1"))
        self.assertEqual(sn.state.interval, 2)
        await sn.handle_update(cbq(OWNER, "m:notify"))
        self.assertEqual(sn.state.mode, "notify")
        await sn.handle_update(cbq(OWNER, "m:buy"))
        await sn.handle_update(cbq(OWNER, "m:buy!"))
        self.assertEqual(sn.state.mode, "notify")
        self.assertIn("ALLOW_AUTOPAY", tg.answers[-1][1])
        await sn.handle_update(cbq(OWNER, "n:sold_out"))
        self.assertFalse(sn.state.notify_on("sold_out"))
        await sn.handle_update(cbq(OWNER, "p:25skc012"))
        self.assertEqual(sn.state.active_plans(), ["25skb012"])
        await sn.handle_update(cbq(OWNER, "dc:rbx"))
        await sn.handle_update(cbq(OWNER, "dc:gra"))
        self.assertEqual(sn.state.data["datacenters"], ["gra", "rbx"])
        await sn.handle_update(cbq(OWNER, "dc:*"))
        self.assertEqual(sn.state.data["datacenters"], [])
        await sn.handle_update(cbq(OWNER, "sp"))
        self.assertEqual(sn.state.data["speed"], "fast")
        await sn.handle_update(cbq(OWNER, "pr"))
        self.assertEqual(sn.state.data["max_price"], 20.0)
        await sn.handle_update(cbq(OWNER, "t:one_server"))
        self.assertFalse(sn.state.data["one_server"])
        for view in ("v:interval", "v:triggers", "v:dcs", "v:notify", "v:main", "v:bogus", "zzz"):
            await sn.handle_update(cbq(OWNER, view))
        self.assertEqual(len(tg.answers), 21)
        saved = json.loads(Path(sn.config.state_path).read_text())
        self.assertEqual(saved["interval"], 2)
        self.assertFalse(saved["enabled"])

    async def test_buy_mode_when_allowed(self):
        sn, tg = self.make(owner_id=OWNER, allow_autopay=True)
        await sn.handle_update(cbq(OWNER, "m:buy"))
        self.assertEqual(sn.state.mode, "reserve")
        self.assertIn("Switch to auto-buy", tg.edits[-1][2])
        await sn.handle_update(cbq(OWNER, "m:buy!"))
        self.assertEqual(sn.state.mode, "buy")

    async def test_stock_notify_reserve_once_and_sold_out(self):
        ovh = FakeOvh()
        sn, tg = self.make(ovh=ovh, owner_id=OWNER)
        await sn.check_once()
        self.assertEqual(tg.sent, [])
        self.feed["25skb012"] = {"gra": "1H-low"}
        self.feed["25skc012"] = {"gra": "1H-high"}
        await sn.check_once()
        texts = [t for _, t, _ in tg.sent]
        self.assertIn("Kimsufi in stock", texts[0])
        self.assertIn("Reservation created", texts[1])
        self.assertEqual(len(ovh.posted("/checkout")), 1)
        self.assertEqual(ovh.posted("/eco")[0]["planCode"], "25skb012")
        self.assertTrue(sn.state.lock_until("25skc012"))
        await sn.check_once()
        self.assertEqual(len(ovh.posted("/checkout")), 1)
        self.assertEqual(len(tg.sent), 2)
        self.feed["25skb012"] = {"gra": "unavailable"}
        self.feed["25skc012"] = {"gra": "unavailable"}
        await sn.check_once()
        self.assertIn("Sold out again", tg.sent[-1][1])
        self.assertEqual(sn.state.data["orders"][-1]["order_id"], "555")

    async def test_failed_order_retries_limited(self):
        ovh = FakeOvh(price=50.0)
        sn, tg = self.make(ovh=ovh, owner_id=OWNER)
        sn.state.data["plans"]["25skc012"] = False
        self.feed["25skb012"] = {"gra": "1H-low"}
        t = [10_000.0]
        import main as main_mod
        orig = main_mod.time.time
        main_mod.time.time = lambda: t[0]
        try:
            for _ in range(6):
                await sn.check_once()
                t[0] += 61
        finally:
            main_mod.time.time = orig
        self.assertEqual(len([m for m, p, _ in ovh.log if m == "POST" and p == "/order/cart"]), MAX_ATTEMPTS)
        self.assertEqual(ovh.posted("/checkout"), [])
        self.assertIsNone(sn.state.lock_until("25skb012"))

    async def test_notify_mode_and_muted(self):
        ovh = FakeOvh()
        sn, tg = self.make(ovh=ovh, owner_id=OWNER)
        sn.state.data["mode"] = "notify"
        sn.state.data["notify"]["available"] = False
        self.feed["25skb012"] = {"gra": "24H"}
        await sn.check_once()
        self.assertEqual(tg.sent, [])
        self.assertEqual(ovh.log, [])
        self.assertEqual(sn.runtime.stock, {"25skb012@gra": "24H"})

    async def test_no_keys_notifies_only(self):
        sn, tg = self.make(owner_id=OWNER)
        self.feed["25skb012"] = {"gra": "1H-low"}
        await sn.check_once()
        self.assertEqual(len(tg.sent), 1)
        self.assertIn("OVH API keys are not set", tg.sent[0][1])

    async def test_error_backoff_and_alerts(self):
        sn, tg = self.make(owner_id=OWNER)
        self.status = 503
        for _ in range(ERROR_ALERT_STREAK + 2):
            with self.assertRaises(FetchError) as ctx:
                await sn.check_once()
            await sn._on_fetch_error(ctx.exception)
        self.assertEqual(len(tg.sent), 1)
        self.assertIn("API fails", tg.sent[0][1])
        self.assertGreater(sn.runtime.backoff, sn.state.interval)
        self.assertLessEqual(sn.runtime.backoff, 300)
        self.status = 200
        await sn.check_once()
        self.assertIn("answers again", tg.sent[-1][1])
        self.assertEqual(sn.runtime.error_streak, 0)

    async def test_defaults_unchanged(self):
        sn, tg = self.make(owner_id=OWNER)
        d = sn.state.data
        self.assertEqual(
            (d["mode"], d["interval"], d["region"], d["datacenters"], d["speed"], d["max_price"], d["one_server"], d["enabled"]),
            ("reserve", 5, "eu", [], "any", 15.0, True, True),
        )
        self.assertEqual(sn.state.active_plans(), ["25skb012", "25skc012"])
        self.assertEqual(d["custom_plans"], {})
        self.assertTrue(all(d["notify"].values()))
        self.feed["25skb012"] = {"bhs": "1H-low", "syd": "1H-low"}
        await sn.check_once()
        self.assertEqual(tg.sent, [])
        self.feed["25skb012"] = {"bhs": "1H-low", "gra": "24H"}
        await sn.check_once()
        self.assertEqual(sn.runtime.stock, {"25skb012@gra": "24H"})

    async def test_region_and_datacenters(self):
        sn, tg = self.make(owner_id=OWNER)
        self.feed["25skb012"] = {"bhs": "1H-low", "syd": "24H", "gra": "unavailable"}
        await sn.handle_update(cbq(OWNER, "rg"))
        self.assertEqual(sn.state.region, "na")
        await sn.check_once()
        self.assertEqual(sn.runtime.stock, {"25skb012@bhs": "1H-low"})
        for _ in range(2):
            await sn.handle_update(cbq(OWNER, "rg"))
        self.assertEqual(sn.state.region, "all")
        await sn.check_once()
        self.assertEqual(set(sn.runtime.stock), {"25skb012@bhs", "25skb012@syd"})
        await sn.handle_update(cbq(OWNER, "rg"))
        self.assertEqual(sn.state.region, "eu")
        await sn.handle_update(cbq(OWNER, "dc:syd"))
        self.assertEqual(sn.state.data["datacenters"], ["syd"])
        await sn.check_once()
        self.assertEqual(sn.runtime.stock, {"25skb012@syd": "24H"})
        await sn.handle_update(cbq(OWNER, "v:dcs"))
        self.assertIn("SYD", tg.edits[-1][2])
        await sn.handle_update(cbq(OWNER, "dc:not-a-dc"))
        self.assertEqual(sn.state.data["datacenters"], ["syd"])

    async def test_catalog_adds_any_plan_and_persists(self):
        sn, tg = self.make(owner_id=OWNER, ovh_subsidiary="WE", currency="USD")
        await sn.handle_update(cbq(OWNER, "v:catalog"))
        self.assertEqual(self.catalog_subsidiary, "WE")
        codes = [c for c, _, _ in sn.runtime.catalog]
        self.assertEqual(codes, ["25skb012", "25skc012", "24sk302", "24sk302-sgp"])
        self.assertEqual(sn.runtime.catalog[3][1], "KS-3 SGP")
        await sn.handle_update(cbq(OWNER, "pc:24sk302-sgp"))
        self.assertIn("24sk302-sgp", sn.state.active_plans())
        self.assertIn("Above the price limit", tg.edits[-1][2])
        await sn.handle_update(cbq(OWNER, "pc:25skc012"))
        self.assertNotIn("25skc012", sn.state.active_plans())
        self.assertIn("25skc012", sn.state.all_plans())
        await sn.handle_update(cbq(OWNER, "pc:unknown"))
        self.assertNotIn("unknown", sn.state.all_plans())
        st2 = State(sn.config)
        self.assertEqual(st2.active_plans(), ["25skb012", "24sk302-sgp"])
        self.assertEqual(st2.label("24sk302-sgp"), "KS-3 SGP")
        await sn.handle_update(cbq(OWNER, "p:24sk302-sgp"))
        self.assertNotIn("24sk302-sgp", sn.state.active_plans())
        self.assertIn("24sk302-sgp", sn.state.all_plans())
        await sn.handle_update(cbq(OWNER, "pc:24sk302-sgp"))
        self.assertNotIn("24sk302-sgp", sn.state.all_plans())
        self.feed["24sk302"] = {"sgp": "1H-low"}
        sn.state.toggle_plan("24sk302", "KS-3")
        sn.state.data["region"] = "apac"
        await sn.check_once()
        self.assertIn("KS-3 (24sk302) SGP", tg.sent[-1][1])

    async def test_dry_run_from_panel(self):
        ovh = FakeOvh()
        sn, tg = self.make(ovh=ovh, owner_id=OWNER)
        sn.runtime.ovh_status = "ok"
        await sn.handle_update(cbq(OWNER, "dry"))
        self.assertIn("Dry run started", tg.answers[-1][1])
        for _ in range(20):
            await asyncio.sleep(0.05)
            if any("Dry run" in t for _, t, _ in tg.sent):
                break
        report = [t for _, t, _ in tg.sent if "Dry run" in t][0]
        self.assertIn("2/2 passed", report)
        self.assertEqual(ovh.posted("/checkout"), [])
        self.assertEqual(len(ovh.posted("/assign")), 2)

    async def test_dry_run_targets_and_anonymous(self):
        sn, tg = self.make(owner_id=OWNER)
        self.feed["25skc012"] = {"bhs": "unavailable"}
        seen = []
        handler = self.handler

        def spy(request):
            seen.append(request.url.params.get("planCode"))
            return handler(request)

        sn.http._transport = httpx.MockTransport(spy)
        targets, notes = await sn.dry_run_targets(["25skb012", "25skc012", "25skb012@bhs"])
        self.assertEqual(sorted(seen), ["25skb012", "25skc012"])
        self.assertEqual([t.key for t in targets], ["25skb012@gra", "25skb012@bhs"])
        self.assertEqual(targets[1].fqn, FQN_B)
        self.assertIn("no datacenter matches", notes[0])
        self.assertIsNone(sn.ovh)
        self.assertEqual(sn.dry_run_text([], notes).count("⚠️"), 1)

    async def test_ovh_key_check(self):
        ovh = FakeOvh()
        sn, tg = self.make(ovh=ovh, owner_id=OWNER)
        self.assertEqual(await sn.check_ovh(), "ok")
        self.assertEqual(tg.sent, [])

        class NoRights(FakeOvh):
            async def get(self, path, params=None):
                raise OvhApiError(403, "This call has not been granted", "https://x/order/cart")

        sn2, tg2 = self.make(ovh=NoRights(), owner_id=OWNER)
        status = await sn2.check_ovh()
        self.assertEqual(status, "cannot use carts (403: This call has not been granted)")
        self.assertIn("Reservations will fail", tg2.sent[0][1])
        await sn2.check_ovh()
        self.assertEqual(len(tg2.sent), 1)
        self.assertIn("cannot use carts", sn2.panel.render("main")[0])

    async def test_check_now_button(self):
        sn, tg = self.make(owner_id=OWNER)
        self.feed["25skc012"] = {"gra": "72H"}
        await sn.handle_update(cbq(OWNER, "chk"))
        self.assertIn("25skc012@gra", tg.answers[-1][1])

    async def test_poll_loop_respects_interval_and_pause(self):
        sn, tg = self.make(owner_id=OWNER)
        sn.state.data["interval"] = 2
        task = asyncio.create_task(sn._poll_loop())
        await asyncio.sleep(0.3)
        self.assertEqual(sn.runtime.checks, 1)
        sn.state.data["enabled"] = False
        sn.wake()
        await asyncio.sleep(0.3)
        checks = sn.runtime.checks
        await asyncio.sleep(0.3)
        self.assertEqual(sn.runtime.checks, checks)
        sn.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class ConfigTests(unittest.TestCase):
    def test_env(self):
        keys = {"OWNER_ID": "123", "OWNER_USERNAME": "@OwnerUser", "POLL_INTERVAL": "1", "DEFAULT_MODE": "x", "NICE": "50"}
        old = {k: os.environ.get(k) for k in keys}
        os.environ.update(keys)
        try:
            cfg = load_config()
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(cfg.owner_id, 123)
        self.assertEqual(cfg.owner_username, "owneruser")
        self.assertEqual(cfg.default_interval, 2)
        self.assertEqual(cfg.default_mode, "reserve")
        self.assertEqual(cfg.nice, 19)
        self.assertFalse(cfg.allow_autopay)


if __name__ == "__main__":
    unittest.main()
