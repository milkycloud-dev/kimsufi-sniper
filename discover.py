"""Standalone discovery tool: list all Kimsufi plans and their availability.

Run: python discover.py
"""

from __future__ import annotations

import asyncio

import httpx

AVAILABILITY_URL = "https://eu.api.ovh.com/v1/dedicated/server/datacenter/availabilities"

EU_DCS = {"gra", "rbx", "sbg", "par", "fra", "waw", "lim", "lon", "eri", "ams", "mad", "mil"}
EXCLUDED_DCS = {"bhs", "vin", "hil", "syd", "sgp", "yny"}


async def main() -> None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        print("Fetching OVH availability data...")
        resp = await client.get(AVAILABILITY_URL)
        resp.raise_for_status()
        data = resp.json()

    print(f"Total entries: {len(data)}")
    print()

    kimsufi: list[dict] = []
    for entry in data:
        pc = entry.get("planCode", "")
        if "sk" in pc.lower():
            # Skip regional variants (sgp, syd, etc.)
            if any(suffix in pc.lower() for suffix in ["-sgp", "-syd", "-yny"]):
                continue
            kimsufi.append(entry)

    print(f"Kimsufi plans (EU-relevant): {len(kimsufi)}")
    print("=" * 120)
    print(f"{'planCode':<25} | {'FQN':<60} | {'EU Availability'}")
    print("-" * 120)

    # Group by planCode
    by_plan: dict[str, list[dict]] = {}
    for e in kimsufi:
        pc = e.get("planCode", "")
        if pc not in by_plan:
            by_plan[pc] = []
        by_plan[pc].append(e)

    for plan_code in sorted(by_plan.keys()):
        entries = by_plan[plan_code]
        for e in entries:
            fqn = e.get("fqn", "")
            eu_avail = []
            for dc_info in e.get("datacenters", []):
                dc = dc_info.get("datacenter", "").lower()
                avail = dc_info.get("availability", "")
                if dc in EXCLUDED_DCS:
                    continue
                if avail.lower() not in ("unavailable", "unknown", ""):
                    eu_avail.append(f"{dc}={avail}")

            status = ", ".join(eu_avail) if eu_avail else "\033[90munavailable\033[0m"
            if eu_avail:
                status = f"\033[92m{status}\033[0m"
            print(f"{plan_code:<25} | {fqn:<60} | {status}")

    print()
    print("Unique plan codes with EU availability:")
    available_plans = set()
    for plan_code, entries in by_plan.items():
        for e in entries:
            for dc_info in e.get("datacenters", []):
                dc = dc_info.get("datacenter", "").lower()
                avail = dc_info.get("availability", "")
                if dc not in EXCLUDED_DCS and avail.lower() not in ("unavailable", "unknown", ""):
                    available_plans.add(plan_code)

    if available_plans:
        for p in sorted(available_plans):
            print(f"  {p}")
        print(f"\nSuggested TARGET_PLAN_CODES={','.join(sorted(available_plans))}")
    else:
        print("  None currently available (this is normal for KS-B / KS-C)")

    print()
    print("Key plan code mappings (from live catalog):")
    print("  25skb012    = KS-B   (Xeon E5-1620v2,  9.99 EUR, $11.10)  <-- PRIMARY TARGET")
    print("  25skc012    = KS-C   (Xeon E5-1650v2, 11.99 EUR, $13.30)  <-- SECONDARY TARGET")
    print("  24sk102     = KS-1   (Xeon-D 1520, 16.99 EUR/mo)")
    print("  24sk202     = KS-2   (Xeon-D 1540, 18.99 EUR/mo)")
    print("  24sk302     = KS-3   (Xeon E3-1245v5, 18.99 EUR/mo)")
    print("  24sk40-v1   = KS-4   (Xeon E3-1230v6, 16.99 EUR/mo)")
    print("  24sk50-v1   = KS-5   (Xeon E3-1270v6, 17.99 EUR/mo)")
    print("  27skle01-v1 = KS-LE-1 (budget/LE tier)")
    print("  27skle02-v1 = KS-LE-2")
    print("  27skle03-v1 = KS-LE-3")
    print("  27skle04-v1 = KS-LE-4")
    print("  27skle05-v1 = KS-LE-5")
    print("  27skle06-v1 = KS-LE-6")
    print()
    print("Note: KS-B and KS-C from eco.ovhcloud.com may map to different")
    print("plan codes depending on hardware refresh. Check the Eco catalog:")
    print("  GET https://eu.api.ovh.com/v1/order/catalog/public/eco?ovhSubsidiary=IE")


if __name__ == "__main__":
    asyncio.run(main())
