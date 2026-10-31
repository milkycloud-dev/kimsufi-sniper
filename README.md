<p align="center"><img src="assets/icon.png" width="128" height="128" alt="Kimsufi Sniper icon"></p>

<h1 align="center">Kimsufi Sniper</h1>

<p align="center">Telegram bot that watches OVH Kimsufi stock (KS-B and KS-C by default), alerts its owner the moment a server appears and can reserve it automatically. Controlled from a private panel inside Telegram.</p>

<p align="center">
  <a href="https://github.com/milkycloud-dev/kimsufi-sniper/actions/workflows/build.yml"><img src="https://github.com/milkycloud-dev/kimsufi-sniper/actions/workflows/build.yml/badge.svg" alt="Build"></a>
  <a href="https://github.com/milkycloud-dev/kimsufi-sniper/releases/latest"><img src="https://img.shields.io/github/v/release/milkycloud-dev/kimsufi-sniper" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License: MIT"></a>
</p>

<p align="center"><a href="#english">English</a> | <a href="#русский">Русский</a></p>

<a id="english"></a>

## English

### Overview

Cheap Kimsufi servers sell out within minutes of appearing. The bot polls the public OVH availability API for the plans you choose, in European datacenters only, and messages you as soon as one turns in stock. In reserve mode it also places the order through the OVH API without paying for it, so the server is waiting for you with a payment link.

### Features

- **Control panel** on `/start`: on/off, operating mode, check interval, triggers and notifications, all as inline buttons. Changes apply at once and survive restarts.
- **Owner only.** The bot accepts a single Telegram user id. Anyone else gets no reply at all, button presses from other accounts are dropped, and the bot leaves any group it is added to.
- **Three modes:**
  - *Notify*: alerts only.
  - *Reserve* (default): creates the order with `autoPayWithPreferredPaymentMethod=false` and sends you the payment link.
  - *Buy*: pays with the default payment method. Locked unless `ALLOW_AUTOPAY=true`, and asks for confirmation in the panel.
- **Triggers:** plans, datacenters, delivery time (any or 1H only), price limit (excluding tax) checked in the cart before ordering, and "one server only", which pauses further reservations for 24 h after a success.
- **Notifications** (all on by default): in stock, sold out again, reservations and orders, API errors, start and stop, daily summary.
- **Gentle on the API:** one small request per plan per check, exponential backoff on HTTP 429 and 5xx (up to 5 min), at most 3 order attempts per stock event.
- **Hardened container:** read-only root filesystem, all capabilities dropped, non-root user, memory, CPU and process limits.

### How often can it check?

OVH documents no rate limit for `GET /v1/dedicated/server/datacenter/availabilities`, so it was measured:

| Test | Result |
|------|--------|
| 1, 2 and 4 requests/s for 30 s each, filtered by `planCode` | all HTTP 200, about 0.1 s, no 429, no rate-limit headers |
| Full feed without a filter | 4.3 MB per request (9017 rows); about 74 GB per day at a 5 s interval |
| 25 full snapshots over 5 minutes | no status change in any row: the data refreshes on a scale of minutes |
| Response headers | `cache-control: no-cache, no-store` |

The bot therefore always filters by plan (216 bytes per request). **5 s is the default and the recommendation** (24 requests/min for two plans). The panel offers 2 to 60 s; 2 s is the floor (60 requests/min for two plans, still four times below the tested burst). Faster checks gain little because the source data does not change faster.

### Requirements

- Docker with Compose, or Python 3.12
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- For reserve and buy modes: OVH API keys from [eu.api.ovh.com/createToken](https://eu.api.ovh.com/createToken/) (or [ca.api.ovh.com/createToken](https://ca.api.ovh.com/createToken/) for USD and CA accounts) with `GET` and `POST` on `/order/cart` and `/order/cart/*`, `DELETE` on `/order/cart/*` and `GET` on `/me`

### Setup

```bash
cp .env.example .env
# fill in TELEGRAM_BOT_TOKEN and OWNER_ID (or OWNER_USERNAME), plus the OVH keys
docker compose up -d --build
docker compose logs -f
```

Open the bot in Telegram and send `/start`. If you set only `OWNER_USERNAME`, the first `/start` from that username in a private chat pins its numeric id for good; after that the username is no longer trusted.

### Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `TELEGRAM_BOT_TOKEN` | | Bot token |
| `OWNER_ID` | | Numeric Telegram id of the only allowed user |
| `OWNER_USERNAME` | | Used once to pin `OWNER_ID` when it is not set |
| `OVH_APP_KEY`, `OVH_APP_SECRET`, `OVH_CONSUMER_KEY` | | OVH API keys; without them the bot only notifies |
| `OVH_ENDPOINT` | `ovh-eu` | `ovh-eu`, `ovh-ca` or `ovh-us` |
| `OVH_SUBSIDIARY` | `IE` | Subsidiary used for the cart; `WE` or `WS` on `ovh-ca` bill in USD |
| `TARGET_PLANS` | `25skb012:KS-B,25skc012:KS-C` | Plans as `code:label` |
| `DEFAULT_MODE` | `reserve` | First-start mode |
| `POLL_INTERVAL` | `5` | First-start interval in seconds |
| `MAX_PRICE` | `15.0` | First-start price limit, excluding tax, in the account currency |
| `PRICE_CURRENCY` | `EUR` | Currency shown next to prices: `USD` for the WE and WS subsidiaries |
| `RESERVE_COOLDOWN_HOURS` | `24` | Pause after a successful reservation |
| `ALLOW_AUTOPAY` | `false` | Unlocks buy mode |
| `NICE` | `0` | Process nice value, 0 to 19 |
| `STATE_PATH` | `data/state.json` | Where panel settings are stored |

Values marked "first-start" only seed `state.json`; afterwards the panel wins.

### Tests

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

### License

MIT, see [LICENSE](LICENSE).

<a id="русский"></a>

## Русский

### Обзор

Дешёвые серверы Kimsufi раскупают за минуты. Бот опрашивает публичный API наличия OVH по выбранным тарифам, только в европейских дата-центрах, и сразу пишет владельцу, когда сервер появляется. В режиме резерва он ещё и оформляет заказ через API OVH без оплаты: сервер ждёт вас со ссылкой на оплату.

### Возможности

- **Панель управления** по `/start`: вкл/выкл, режим работы, частота проверки, триггеры и уведомления кнопками. Изменения применяются сразу и сохраняются между перезапусками.
- **Только владелец.** Бот принимает один Telegram id. Остальным он не отвечает вообще, нажатия кнопок с чужих аккаунтов отбрасываются, из групп бот выходит сам.
- **Три режима:**
  - *Уведомления*: только сообщения.
  - *Резерв* (по умолчанию): заказ создаётся с `autoPayWithPreferredPaymentMethod=false`, бот присылает ссылку на оплату.
  - *Покупка*: оплата основным способом оплаты. Заблокирована без `ALLOW_AUTOPAY=true` и требует подтверждения в панели.
- **Триггеры:** тарифы, дата-центры, срок поставки (любой или только 1H), лимит цены с проверкой в корзине до заказа, «только один сервер»: после успешного резерва новые резервы стоят на паузе 24 ч.
- **Уведомления** (все включены по умолчанию): появился в наличии, снова раскуплен, резервы и заказы, ошибки API, старт и стоп, итоги дня.
- **Бережно к API:** один маленький запрос на тариф за проверку, экспоненциальная пауза при HTTP 429 и 5xx (до 5 мин), не больше 3 попыток заказа на одно появление.
- **Защищённый контейнер:** корень только для чтения, без capabilities, не root, лимиты памяти, CPU и процессов.

### Как часто можно проверять?

Лимит для `GET /v1/dedicated/server/datacenter/availabilities` в документации OVH не указан, поэтому он измерен:

| Тест | Результат |
|------|-----------|
| 1, 2 и 4 запроса/с по 30 с, с фильтром `planCode` | все HTTP 200, около 0,1 с, ни одного 429, заголовков лимита нет |
| Полный список без фильтра | 4,3 МБ на запрос (9017 строк), около 74 ГБ в сутки при интервале 5 с |
| 25 полных снимков за 5 минут | ни одного изменения статуса: данные обновляются раз в несколько минут |
| Заголовки ответа | `cache-control: no-cache, no-store` |

Поэтому бот всегда фильтрует по тарифу (216 байт на запрос). **5 с по умолчанию и рекомендуется** (24 запроса/мин на два тарифа). В панели доступно от 2 до 60 с; 2 с: нижняя граница (60 запросов/мин на два тарифа, вчетверо ниже проверенного всплеска). Чаще проверять почти бесполезно: исходные данные быстрее не меняются.

### Требования

- Docker с Compose или Python 3.12
- Токен бота от [@BotFather](https://t.me/BotFather)
- Для резерва и покупки: ключи API OVH с [eu.api.ovh.com/createToken](https://eu.api.ovh.com/createToken/), (или [ca.api.ovh.com/createToken](https://ca.api.ovh.com/createToken/) для долларовых и канадских аккаунтов), права `GET` и `POST` на `/order/cart` и `/order/cart/*`, `DELETE` на `/order/cart/*` и `GET` на `/me`

### Установка

```bash
cp .env.example .env
# заполнить TELEGRAM_BOT_TOKEN и OWNER_ID (или OWNER_USERNAME), плюс ключи OVH
docker compose up -d --build
docker compose logs -f
```

Откройте бота в Telegram и отправьте `/start`. Если задан только `OWNER_USERNAME`, первый `/start` с этого имени в личном чате навсегда закрепляет числовой id; дальше имени пользователя бот не доверяет.

### Настройки

Переменные окружения перечислены в английской части и в `.env.example`. Значения режима, интервала и лимита цены задают только первый запуск, дальше действуют настройки из панели (`data/state.json`).

### Тесты

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

### Лицензия

MIT, см. [LICENSE](LICENSE).
