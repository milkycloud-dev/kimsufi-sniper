from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)


DATACENTERS = {
    "gra": ("eu", "Gravelines"),
    "rbx": ("eu", "Roubaix"),
    "sbg": ("eu", "Strasbourg"),
    "par": ("eu", "Paris"),
    "fra": ("eu", "Frankfurt"),
    "waw": ("eu", "Warsaw"),
    "lon": ("eu", "London"),
    "eri": ("eu", "Erith"),
    "lim": ("eu", "Limburg"),
    "bhs": ("na", "Beauharnois"),
    "vin": ("na", "Vint Hill"),
    "hil": ("na", "Hillsboro"),
    "sgp": ("apac", "Singapore"),
    "syd": ("apac", "Sydney"),
    "ynm": ("apac", "Mumbai"),
}

REGIONS = {
    "eu": "Europe",
    "na": "North America",
    "apac": "Asia-Pacific",
    "all": "Worldwide",
}

_REGION_PREFIXES = (("eu-", "eu"), ("ca-", "na"), ("us-", "na"), ("ap-", "apac"))


def dc_region(dc: str) -> str:
    dc = dc.lower()
    if dc in DATACENTERS:
        return DATACENTERS[dc][0]
    for prefix, region in _REGION_PREFIXES:
        if dc.startswith(prefix):
            return region
    return "other"


DEFAULT_PLANS = "25skb012:KS-B,25skc012:KS-C"

INTERVAL_CHOICES = (2, 3, 5, 10, 15, 30, 60)
MIN_INTERVAL = INTERVAL_CHOICES[0]
MAX_BACKOFF = 300

PRICE_CHOICES = (12.0, 13.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 75.0, 100.0)

MODES = ("notify", "reserve", "buy")


def _bool(key: str, default: str = "false") -> bool:
    return os.getenv(key, default).strip().lower() in ("1", "true", "yes", "on")


def _plans(raw: str) -> dict[str, str]:
    plans: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        code, _, label = item.partition(":")
        code = code.strip().lower()
        plans[code] = label.strip() or code
    return plans


def _owner_id(raw: str) -> int | None:
    raw = raw.strip()
    return int(raw) if raw.lstrip("-").isdigit() else None


@dataclass(frozen=True)
class Config:
    ovh_app_key: str = ""
    ovh_app_secret: str = ""
    ovh_consumer_key: str = ""
    ovh_endpoint: str = "ovh-eu"
    ovh_subsidiary: str = "IE"

    telegram_bot_token: str = ""
    owner_id: int | None = None
    owner_username: str = ""

    plans: dict[str, str] = field(default_factory=lambda: _plans(DEFAULT_PLANS))
    allow_autopay: bool = False
    default_mode: str = "reserve"
    default_interval: int = 5
    default_max_price: float = 15.0
    default_region: str = "eu"
    currency: str = "EUR"
    reserve_cooldown_hours: int = 24
    state_path: str = "data/state.json"
    nice: int = 0
    log_level: str = "INFO"

    @property
    def has_ovh_credentials(self) -> bool:
        return bool(self.ovh_app_key and self.ovh_app_secret and self.ovh_consumer_key)

    @property
    def ovh_api_base(self) -> str:
        endpoints = {
            "ovh-eu": "https://eu.api.ovh.com/1.0",
            "ovh-ca": "https://ca.api.ovh.com/1.0",
            "ovh-us": "https://api.us.ovhcloud.com/1.0",
        }
        return endpoints.get(self.ovh_endpoint, endpoints["ovh-eu"])


def load_config() -> Config:
    mode = os.getenv("DEFAULT_MODE", "reserve").strip().lower()
    interval = int(os.getenv("POLL_INTERVAL", "5"))
    region = os.getenv("DEFAULT_REGION", "eu").strip().lower()
    return Config(
        ovh_app_key=os.getenv("OVH_APP_KEY", ""),
        ovh_app_secret=os.getenv("OVH_APP_SECRET", ""),
        ovh_consumer_key=os.getenv("OVH_CONSUMER_KEY", ""),
        ovh_endpoint=os.getenv("OVH_ENDPOINT", "ovh-eu"),
        ovh_subsidiary=os.getenv("OVH_SUBSIDIARY", "IE").upper(),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        owner_id=_owner_id(os.getenv("OWNER_ID", "")),
        owner_username=os.getenv("OWNER_USERNAME", "").strip().lstrip("@").lower(),
        plans=_plans(os.getenv("TARGET_PLANS", DEFAULT_PLANS)) or _plans(DEFAULT_PLANS),
        allow_autopay=_bool("ALLOW_AUTOPAY"),
        default_mode=mode if mode in MODES else "reserve",
        default_interval=max(MIN_INTERVAL, interval),
        default_max_price=float(os.getenv("MAX_PRICE", os.getenv("MAX_PRICE_EUR", "15.0"))),
        default_region=region if region in REGIONS else "eu",
        currency=os.getenv("PRICE_CURRENCY", "EUR").strip().upper() or "EUR",
        reserve_cooldown_hours=max(1, int(os.getenv("RESERVE_COOLDOWN_HOURS", "24"))),
        state_path=os.getenv("STATE_PATH", "data/state.json"),
        nice=max(0, min(19, int(os.getenv("NICE", "0")))),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
