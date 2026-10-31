# Changelog

## 1.0.1

- Price limit and messages use the account currency (`PRICE_CURRENCY`, `MAX_PRICE`), so USD accounts on `ovh-ca` (subsidiaries WE and WS) show $11.10 for KS-B and $13.30 for KS-C correctly.
- Setup notes for `ovh-ca` keys and the exact cart rights the key needs.

## 1.0.0

- Telegram control panel on `/start`: on/off switch, operating mode (notify, reserve, buy), check interval, triggers (plans, datacenters, delivery time, price limit, one server only) and per-type notifications.
- Owner-only access: the bot answers one Telegram user id, ignores everyone else without a reply and leaves any group it is added to.
- Auto-reserve mode: creates the OVH order without payment and sends the payment link. Auto-buy stays locked unless `ALLOW_AUTOPAY=true`.
- Order flow adds the mandatory eco options (memory, storage, bandwidth) and the `region` setting that OVH requires for KS-B and KS-C.
- Availability is queried per plan (about 216 bytes per request instead of the 4.3 MB full feed), with exponential backoff on HTTP 429 and 5xx.
- Settings survive restarts in `data/state.json`.
- Hardened container: read-only root, no capabilities, non-root user, memory and process limits, low CPU priority.
