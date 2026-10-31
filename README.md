<p align="center"><img src="assets/icon.png" width="128" height="128" alt="Kimsufi Sniper icon"></p>

<h1 align="center">Kimsufi Sniper</h1>

<p align="center">Telegram bot that watches OVH Kimsufi stock for any plan in any OVH location, alerts its owner the moment a server appears and can reserve it automatically. Controlled from a private panel inside Telegram.</p>

<p align="center">
  <a href="https://github.com/milkycloud-dev/kimsufi-sniper/actions/workflows/build.yml"><img src="https://github.com/milkycloud-dev/kimsufi-sniper/actions/workflows/build.yml/badge.svg" alt="Build"></a>
  <a href="https://github.com/milkycloud-dev/kimsufi-sniper/releases/latest"><img src="https://img.shields.io/github/v/release/milkycloud-dev/kimsufi-sniper" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License: MIT"></a>
</p>

<p align="center"><a href="#english">English</a> | <a href="#русский">Русский</a></p>

<a id="english"></a>

## English

### Overview

Cheap Kimsufi servers sell out within minutes of appearing. The bot polls the public OVH availability API for the plans you choose, in the regions or datacenters you choose, and messages you as soon as one turns in stock. In reserve mode it also places the order through the OVH API without paying for it, so the server is waiting for you with a payment link.

Every Kimsufi plan that your OVH subsidiary sells can be watched: KS-1 to KS-7, KS-A, KS-B, KS-C, KS-GAME, KS-STOR, the newer KS-1-B and KS-5-A/B, and the Asia-Pacific variants. Out of the box the bot watches **KS-B and KS-C in Europe**; everything else is a tap in the panel.

### Features

- **Control panel** on `/start`: on/off, operating mode, check interval, triggers, plan catalog, notifications and a dry run, all as inline buttons. Changes apply at once and survive restarts.
- **Owner only.** The bot accepts a single Telegram user id. Anyone else gets no reply at all, button presses from other accounts are dropped, and the bot leaves any group it is added to.
- **Any plan.** The plan catalog is read live from the OVH API for your subsidiary, with monthly prices. Plans can also be preset with `TARGET_PLANS`.
- **Any location.** Region filter (Europe, North America, Asia-Pacific or worldwide) or an explicit list of datacenters; datacenters that appear in the feed later, such as new `eu-west-par-*` zones, show up in the panel by themselves.
- **Three modes:**
  - *Notify*: alerts only.
  - *Reserve*: creates the order with `autoPayWithPreferredPaymentMethod=false` and sends you the payment link.
  - *Buy*: pays with the default payment method. Locked unless `ALLOW_AUTOPAY=true`, and asks for confirmation in the panel.
- **Correct orders everywhere:** the bot adds each plan's mandatory memory, storage and bandwidth or traffic options, checks that the datacenter is offered for that plan, and sets the `region` value OVH expects for it.
- **Dry run:** builds a real cart for every watched plan in a matching datacenter, reads the price and deletes the cart. It never checks out. With working keys it also assigns the cart and reads the checkout preview; without them it uses an anonymous cart.
- **Triggers:** plans, region or datacenters, delivery time (any or 1H only), price limit checked in the cart before ordering, and "one server only", which pauses further reservations for 24 h after a success.
- **Notifications** (all on by default): in stock, sold out again, reservations and orders, API errors, start and stop, daily summary. The OVH key is checked at start, so a key without cart rights is reported at once.
- **Gentle on the API:** one small request per plan per check, exponential backoff on HTTP 429 and 5xx (up to 5 min), at most 3 order attempts per stock event.
- **Hardened container:** read-only root filesystem, all capabilities dropped, non-root user, memory, CPU and process limits.

### Plans and locations

| Region | Datacenters |
|--------|-------------|
| Europe | GRA, RBX, SBG, PAR and `eu-west-par-*` zones, FRA, WAW, LON, ERI, LIM |
| North America | BHS, VIN, HIL, `ca-*` and `us-*` zones |
| Asia-Pacific | SGP, SYD, YNM |

Not every plan is sold everywhere. For example, at the time of writing KS-B and KS-C are offered only in GRA, KS-3 in BHS, FRA, GRA, LON, RBX, SBG and WAW, and the `-sgp` and `-syd` plans only in their own datacenter. The dry run lists the datacenters each plan is offered in. Plans that appear in the stock feed but are not in your subsidiary's catalog cannot be ordered; the dry run reports them.

Prices depend on the subsidiary: `IE`, `FR`, `DE` and others bill in EUR on `ovh-eu`, `WE` and `WS` bill in USD on `ovh-ca`. Use the endpoint and subsidiary of the account your API keys belong to.

### How often can it check?

OVH documents no rate limit for `GET /v1/dedicated/server/datacenter/availabilities`, so it was measured:

| Test | Result |
|------|--------|
| 1, 2 and 4 requests/s for 30 s each, filtered by `planCode` | all HTTP 200, about 0.1 s, no 429, no rate-limit headers |
| Full feed without a filter | 4.3 MB per request (9017 rows); about 74 GB per day at a 5 s interval |
| 25 full snapshots over 5 minutes | no status change in any row: the data refreshes on a scale of minutes |
| Response headers | `cache-control: no-cache, no-store` |

The bot therefore always filters by plan (about 216 bytes per request). **5 s is the default and the recommendation.** The panel offers 2 to 60 s and shows the resulting requests per minute for the number of watched plans; keep that number well under 240, the burst that was tested. Faster checks gain little because the source data does not change faster.

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

### Dry run from the command line

```bash
docker compose exec sniper python main.py --dry-run
docker compose exec sniper python main.py --dry-run 24sk302@bhs 24sk302-sgp@sgp 24skgame012
```

Without arguments it tests the watched plans. Arguments are plan codes, optionally with `@datacenter`. The exit code is 0 only when every plan passed.

### Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `TELEGRAM_BOT_TOKEN` | | Bot token |
| `OWNER_ID` | | Numeric Telegram id of the only allowed user |
| `OWNER_USERNAME` | | Used once to pin `OWNER_ID` when it is not set |
| `OVH_APP_KEY`, `OVH_APP_SECRET`, `OVH_CONSUMER_KEY` | | OVH API keys; without them the bot only notifies |
| `OVH_ENDPOINT` | `ovh-eu` | `ovh-eu`, `ovh-ca` or `ovh-us` |
| `OVH_SUBSIDIARY` | `IE` | Subsidiary used for the cart and the catalog; `WE` or `WS` on `ovh-ca` bill in USD |
| `TARGET_PLANS` | `25skb012:KS-B,25skc012:KS-C` | Preset plans as `code:label`; more can be added in the panel |
| `DEFAULT_REGION` | `eu` | First-start region: `eu`, `na`, `apac` or `all` |
| `DEFAULT_MODE` | `reserve` | First-start mode |
| `POLL_INTERVAL` | `5` | First-start interval in seconds |
| `MAX_PRICE` | `15.0` | First-start price limit, excluding tax, in the account currency |
| `PRICE_CURRENCY` | `EUR` | Currency shown next to prices |
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

Дешёвые серверы Kimsufi раскупают за минуты. Бот опрашивает публичный API наличия OVH по выбранным тарифам, в выбранных регионах или дата-центрах, и сразу пишет владельцу, когда сервер появляется. В режиме резерва он ещё и оформляет заказ через API OVH без оплаты: сервер ждёт вас со ссылкой на оплату.

Отслеживать можно любой тариф Kimsufi, который продаёт ваша дочерняя компания OVH: KS-1...KS-7, KS-A, KS-B, KS-C, KS-GAME, KS-STOR, новые KS-1-B и KS-5-A/B, азиатские варианты. По умолчанию бот следит за **KS-B и KS-C в Европе**, остальное включается кнопкой в панели.

### Возможности

- **Панель управления** по `/start`: вкл/выкл, режим, частота проверки, триггеры, каталог тарифов, уведомления и пробный прогон, всё кнопками. Изменения применяются сразу и сохраняются между перезапусками.
- **Только владелец.** Бот принимает один Telegram id. Остальным он не отвечает вообще, нажатия кнопок с чужих аккаунтов отбрасываются, из групп бот выходит сам.
- **Любой тариф.** Каталог читается из API OVH для вашей дочерней компании, с ценами за месяц. Тарифы можно задать и заранее через `TARGET_PLANS`.
- **Любая локация.** Фильтр по региону (Европа, Северная Америка, Азия и Океания или весь мир) или явный список дата-центров; новые дата-центры из выдачи, например зоны `eu-west-par-*`, появляются в панели сами.
- **Три режима:**
  - *Уведомления*: только сообщения.
  - *Резерв*: заказ создаётся с `autoPayWithPreferredPaymentMethod=false`, бот присылает ссылку на оплату.
  - *Покупка*: оплата основным способом оплаты. Заблокирована без `ALLOW_AUTOPAY=true` и требует подтверждения в панели.
- **Правильный заказ в любой локации:** бот добавляет обязательные опции тарифа (память, диск, канал или трафик), проверяет, что дата-центр доступен для этого тарифа, и ставит нужное OVH значение `region`.
- **Пробный прогон:** для каждого тарифа собирается настоящая корзина в подходящем дата-центре, читается цена, корзина удаляется. Заказ не оформляется никогда. С рабочими ключами корзина привязывается к аккаунту и читается предпросмотр оплаты, без них используется анонимная корзина.
- **Триггеры:** тарифы, регион или дата-центры, срок поставки (любой или только 1H), лимит цены с проверкой в корзине до заказа, «только один сервер»: после успешного резерва новые резервы стоят на паузе 24 ч.
- **Уведомления** (все включены по умолчанию): появился в наличии, снова раскуплен, резервы и заказы, ошибки API, старт и стоп, итоги дня. Ключ OVH проверяется при старте, так что ключ без прав на корзины виден сразу.
- **Бережно к API:** один маленький запрос на тариф за проверку, экспоненциальная пауза при HTTP 429 и 5xx (до 5 мин), не больше 3 попыток заказа на одно появление.
- **Защищённый контейнер:** корень только для чтения, без capabilities, не root, лимиты памяти, CPU и процессов.

### Тарифы и локации

| Регион | Дата-центры |
|--------|-------------|
| Европа | GRA, RBX, SBG, PAR и зоны `eu-west-par-*`, FRA, WAW, LON, ERI, LIM |
| Северная Америка | BHS, VIN, HIL, зоны `ca-*` и `us-*` |
| Азия и Океания | SGP, SYD, YNM |

Не каждый тариф продаётся везде. Сейчас, например, KS-B и KS-C есть только в GRA, KS-3 в BHS, FRA, GRA, LON, RBX, SBG и WAW, тарифы `-sgp` и `-syd` только в своём дата-центре. Пробный прогон показывает, где доступен каждый тариф. Тарифы, которые есть в выдаче наличия, но отсутствуют в каталоге вашей дочерней компании, заказать нельзя, прогон об этом сообщит.

Цены зависят от дочерней компании: `IE`, `FR`, `DE` и другие считают в евро на `ovh-eu`, `WE` и `WS` в долларах на `ovh-ca`. Указывайте endpoint и дочернюю компанию того аккаунта, которому принадлежат ключи.

### Как часто можно проверять?

Лимит для `GET /v1/dedicated/server/datacenter/availabilities` в документации OVH не указан, поэтому он измерен:

| Тест | Результат |
|------|-----------|
| 1, 2 и 4 запроса/с по 30 с, с фильтром `planCode` | все HTTP 200, около 0,1 с, ни одного 429, заголовков лимита нет |
| Полный список без фильтра | 4,3 МБ на запрос (9017 строк), около 74 ГБ в сутки при интервале 5 с |
| 25 полных снимков за 5 минут | ни одного изменения статуса: данные обновляются раз в несколько минут |
| Заголовки ответа | `cache-control: no-cache, no-store` |

Поэтому бот всегда фильтрует по тарифу (около 216 байт на запрос). **5 с по умолчанию и рекомендуется.** В панели доступно от 2 до 60 с, рядом показано число запросов в минуту для текущего числа тарифов; держите его заметно ниже 240, проверенного всплеска. Чаще проверять почти бесполезно: исходные данные быстрее не меняются.

### Требования

- Docker с Compose или Python 3.12
- Токен бота от [@BotFather](https://t.me/BotFather)
- Для резерва и покупки: ключи API OVH с [eu.api.ovh.com/createToken](https://eu.api.ovh.com/createToken/) (или [ca.api.ovh.com/createToken](https://ca.api.ovh.com/createToken/) для долларовых и канадских аккаунтов), права `GET` и `POST` на `/order/cart` и `/order/cart/*`, `DELETE` на `/order/cart/*` и `GET` на `/me`

### Установка

```bash
cp .env.example .env
# заполнить TELEGRAM_BOT_TOKEN и OWNER_ID (или OWNER_USERNAME), плюс ключи OVH
docker compose up -d --build
docker compose logs -f
```

Откройте бота в Telegram и отправьте `/start`. Если задан только `OWNER_USERNAME`, первый `/start` с этого имени в личном чате навсегда закрепляет числовой id; дальше имени пользователя бот не доверяет.

### Пробный прогон из командной строки

```bash
docker compose exec sniper python main.py --dry-run
docker compose exec sniper python main.py --dry-run 24sk302@bhs 24sk302-sgp@sgp 24skgame012
```

Без аргументов проверяются отслеживаемые тарифы. Аргументы: коды тарифов, можно с `@дата-центр`. Код выхода 0, только если прошли все.

### Настройки

Переменные окружения перечислены в английской части и в `.env.example`. Режим, интервал, регион и лимит цены из окружения задают только первый запуск, дальше действуют настройки панели (`data/state.json`).

### Тесты

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

### Лицензия

MIT, см. [LICENSE](LICENSE).
