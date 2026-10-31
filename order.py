from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from availability import ServerAvailability
from config import Config, dc_region
from ovh_api import OvhApiError, OvhClient

logger = logging.getLogger(__name__)

DURATION = "P1M"
PRICING = "default"


@dataclass
class OrderResult:
    success: bool
    autopay: bool
    order_id: str | None = None
    order_url: str | None = None
    price: float | None = None
    price_with_tax: float | None = None
    error: str | None = None

    @property
    def summary(self) -> str:
        kind = "Order" if self.autopay else "Reservation"
        if self.success:
            return f"{kind} #{self.order_id} created: {self.order_url}"
        return f"{kind} failed: {self.error}"


def _amount(prices: dict[str, Any], key: str) -> float:
    value = (prices.get(key) or {}).get("value", 0) or 0
    return float(value)


def pick_options(fqn: str, options: list[dict[str, Any]]) -> list[str]:
    parts = fqn.split(".")[1:]
    chosen: dict[str, str] = {}
    for opt in options:
        if not opt.get("mandatory"):
            continue
        family = opt.get("family", "")
        code = opt.get("planCode", "")
        if any(code.startswith(part + "-") or code == part for part in parts):
            chosen[family] = code
        else:
            chosen.setdefault(family, code)
    return list(chosen.values())


class CartError(Exception):
    pass


def pick_region(datacenter: str, allowed: list[str]) -> str:
    if len(allowed) == 1:
        return allowed[0]
    want = "europe" if dc_region(datacenter) == "eu" else "canada"
    if want in allowed:
        return want
    others = [v for v in allowed if v != "europe"]
    return (others or allowed)[0]


async def build_cart(
    client: OvhClient,
    config: Config,
    server: ServerAvailability,
    assign: bool,
    steps: list[str] | None = None,
) -> tuple[str, int]:
    log = steps if steps is not None else []
    cart = await client.post("/order/cart", data={
        "ovhSubsidiary": config.ovh_subsidiary,
        "description": f"kimsufi-sniper {server.plan_code}",
    })
    cart_id = cart["cartId"]
    log.append(f"cart created ({config.ovh_subsidiary})")
    try:
        if assign:
            await client.post(f"/order/cart/{cart_id}/assign")
            log.append("cart assigned to the account")

        item = await client.post(f"/order/cart/{cart_id}/eco", data={
            "planCode": server.plan_code,
            "duration": DURATION,
            "pricingMode": PRICING,
            "quantity": 1,
        })
        item_id = item["itemId"]
        log.append(f"plan {server.plan_code} added")

        options = await client.get(f"/order/cart/{cart_id}/eco/options", params={"planCode": server.plan_code})
        chosen = pick_options(server.fqn, options or [])
        for code in chosen:
            await client.post(f"/order/cart/{cart_id}/eco/options", data={
                "itemId": item_id,
                "planCode": code,
                "duration": DURATION,
                "pricingMode": PRICING,
                "quantity": 1,
            })
        log.append("options: " + (", ".join(chosen) or "none"))

        required = await client.get(f"/order/cart/{cart_id}/item/{item_id}/requiredConfiguration")
        settings = []
        for req in required or []:
            label = req.get("label", "")
            allowed = req.get("allowedValues") or []
            if label == "dedicated_datacenter":
                if allowed and server.datacenter not in allowed:
                    raise CartError(
                        f"datacenter {server.datacenter} is not offered for {server.plan_code} "
                        f"(offered: {', '.join(allowed)})"
                    )
                value = server.datacenter
            elif label == "dedicated_os":
                value = "none_64.en" if not allowed or "none_64.en" in allowed else allowed[0]
            elif label == "region":
                value = pick_region(server.datacenter, allowed) if allowed else "europe"
            elif req.get("required") and allowed:
                value = allowed[0]
            else:
                continue
            await client.post(f"/order/cart/{cart_id}/item/{item_id}/configuration", data={
                "label": label,
                "value": value,
            })
            settings.append(f"{label}={value}")
        log.append("configuration: " + ", ".join(settings))
        return cart_id, item_id
    except Exception:
        await _cleanup_cart(client, cart_id)
        raise


async def place_order(
    client: OvhClient,
    config: Config,
    server: ServerAvailability,
    max_price: float,
    autopay: bool,
) -> OrderResult:
    cart_id: str | None = None
    try:
        cart_id, _ = await build_cart(client, config, server, assign=True)

        summary = await client.get(f"/order/cart/{cart_id}/checkout")
        prices = summary.get("prices", {}) if isinstance(summary, dict) else {}
        price = _amount(prices, "withoutTax")
        price_tax = _amount(prices, "withTax")
        if price <= 0 or price > max_price:
            msg = f"price {price:.2f} {config.currency} outside the limit {max_price:.2f} {config.currency}"
            logger.warning("%s: %s", server.label, msg)
            await _cleanup_cart(client, cart_id)
            return OrderResult(False, autopay, price=price, price_with_tax=price_tax, error=msg)

        order = await client.post(f"/order/cart/{cart_id}/checkout", data={
            "autoPayWithPreferredPaymentMethod": autopay,
            "waiveRetractationPeriod": True,
        })
        result = OrderResult(
            True,
            autopay,
            order_id=str(order.get("orderId", "unknown")),
            order_url=order.get("url", "") or None,
            price=price,
            price_with_tax=price_tax,
        )
        logger.info("%s: %s", server.label, result.summary)
        return result

    except (OvhApiError, CartError) as exc:
        logger.error("Order error for %s: %s", server.label, exc)
        error = str(exc)
    except Exception as exc:
        logger.exception("Unexpected order error for %s", server.label)
        error = f"{type(exc).__name__}: {exc}"
    if cart_id:
        await _cleanup_cart(client, cart_id)
    return OrderResult(False, autopay, error=error)


async def _cleanup_cart(client: OvhClient, cart_id: str) -> None:
    try:
        await client.delete(f"/order/cart/{cart_id}")
    except Exception as exc:
        logger.debug("Cart %s not deleted: %s", cart_id, exc)


@dataclass
class DryRunResult:
    server: ServerAvailability
    ok: bool
    steps: list[str]
    price: float | None = None
    price_with_tax: float | None = None
    authenticated: bool = False
    error: str | None = None


async def dry_run(
    client: OvhClient,
    config: Config,
    server: ServerAvailability,
    max_price: float,
    authenticated: bool,
) -> DryRunResult:
    steps: list[str] = []
    result = DryRunResult(server, False, steps, authenticated=authenticated)
    cart_id: str | None = None
    try:
        cart_id, item_id = await build_cart(client, config, server, assign=authenticated, steps=steps)
        if authenticated:
            summary = await client.get(f"/order/cart/{cart_id}/checkout")
            prices = summary.get("prices", {}) if isinstance(summary, dict) else {}
            result.price = _amount(prices, "withoutTax")
            result.price_with_tax = _amount(prices, "withTax")
            steps.append("checkout preview read, no order placed")
        else:
            item = await client.get(f"/order/cart/{cart_id}/item/{item_id}")
            result.price = sum(float((p.get("price") or {}).get("value", 0) or 0)
                               for p in item.get("prices", []) if p.get("label") == "TOTAL")
            steps.append("item price read (anonymous cart, checkout needs working keys)")
        within = result.price is not None and 0 < result.price <= max_price
        steps.append(
            f"price {result.price:.2f} {config.currency} "
            f"{'within' if within else 'OUTSIDE'} the limit {max_price:.2f} {config.currency}"
        )
        result.ok = within
        if not within:
            result.error = "price outside the limit"
    except (OvhApiError, CartError) as exc:
        result.error = str(exc)
    except Exception as exc:
        logger.exception("Dry run failed for %s", server.label)
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        if cart_id:
            await _cleanup_cart(client, cart_id)
    return result
