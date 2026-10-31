from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from availability import ServerAvailability
from config import Config
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


async def place_order(
    client: OvhClient,
    config: Config,
    server: ServerAvailability,
    max_price: float,
    autopay: bool,
) -> OrderResult:
    cart_id: str | None = None
    try:
        cart = await client.post("/order/cart", data={
            "ovhSubsidiary": config.ovh_subsidiary,
            "description": f"kimsufi-sniper {server.plan_code}",
        })
        cart_id = cart["cartId"]
        await client.post(f"/order/cart/{cart_id}/assign")

        item = await client.post(f"/order/cart/{cart_id}/eco", data={
            "planCode": server.plan_code,
            "duration": DURATION,
            "pricingMode": PRICING,
            "quantity": 1,
        })
        item_id = item["itemId"]

        options = await client.get(f"/order/cart/{cart_id}/eco/options", params={"planCode": server.plan_code})
        for code in pick_options(server.fqn, options or []):
            await client.post(f"/order/cart/{cart_id}/eco/options", data={
                "itemId": item_id,
                "planCode": code,
                "duration": DURATION,
                "pricingMode": PRICING,
                "quantity": 1,
            })

        required = await client.get(f"/order/cart/{cart_id}/item/{item_id}/requiredConfiguration")
        for req in required or []:
            label = req.get("label", "")
            allowed = req.get("allowedValues") or []
            if label == "dedicated_datacenter":
                value = server.datacenter
            elif label == "dedicated_os":
                value = "none_64.en" if not allowed or "none_64.en" in allowed else allowed[0]
            elif label == "region":
                value = "europe" if not allowed or "europe" in allowed else allowed[0]
            elif req.get("required") and allowed:
                value = allowed[0]
            else:
                continue
            await client.post(f"/order/cart/{cart_id}/item/{item_id}/configuration", data={
                "label": label,
                "value": value,
            })

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

    except OvhApiError as exc:
        logger.error("OVH API error for %s: %s", server.label, exc)
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
