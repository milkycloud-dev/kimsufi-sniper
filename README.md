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

Дешёвые серверы Kimsufi раскупают за считанные минуты. Бот следит за наличием выбранных тарифов через публичный API OVH, в нужных регионах или дата-центрах, и сразу присылает владельцу сообщение, как только сервер появляется. В режиме резерва бот сам оформляет заказ через API OVH, но не оплачивает его: вам остаётся только перейти по ссылке и оплатить.

Можно отслеживать любой тариф Kimsufi, доступный для вашего аккаунта OVH: KS-1...KS-7, KS-A, KS-B, KS-C, KS-GAME, KS-STOR, новые KS-1-B и KS-5-A/B, а также азиатские варианты. По умолчанию бот следит за **KS-B и KS-C в Европе**, остальные тарифы включаются одной кнопкой в панели.

### Возможности

- **Панель управления** по команде `/start`: включение и выключение, режим работы, частота проверки, триггеры, каталог тарифов, уведомления и тестовый прогон. Всё настраивается кнопками, изменения вступают в силу сразу и сохраняются после перезапуска.
- **Доступ только для владельца.** Бот работает с одним Telegram id. Другим пользователям он не отвечает, нажатия кнопок с чужих аккаунтов игнорирует, а если его добавить в группу, сразу из неё выходит.
- **Любой тариф.** Каталог с ценами за месяц загружается из API OVH для вашего аккаунта. Тарифы можно также задать заранее в `TARGET_PLANS`.
- **Любая локация.** Можно выбрать регион (Европа, Северная Америка, Азиатско-Тихоокеанский регион или весь мир) или конкретные дата-центры. Новые дата-центры, например зоны `eu-west-par-*`, появляются в панели автоматически.
- **Три режима работы:**
  - *Уведомления*: бот только сообщает о наличии.
  - *Резерв*: заказ создаётся с `autoPayWithPreferredPaymentMethod=false`, бот присылает ссылку на оплату.
  - *Покупка*: заказ сразу оплачивается основным способом оплаты. Режим заблокирован, пока не задано `ALLOW_AUTOPAY=true`, и включается только после подтверждения в панели.
- **Корректный заказ в любой локации.** Бот добавляет обязательные опции тарифа (память, диск, канал или трафик), проверяет, продаётся ли тариф в выбранном дата-центре, и подставляет значение `region`, которое ожидает OVH.
- **Тестовый прогон.** Для каждого тарифа бот собирает настоящую корзину в подходящем дата-центре, получает цену и удаляет корзину. Заказ при этом не оформляется. С рабочими ключами корзина привязывается к аккаунту и запрашивается предварительный расчёт заказа, без ключей используется анонимная корзина.
- **Триггеры:** тарифы, регион или дата-центры, срок выдачи сервера (любой или только 1H), лимит цены, который проверяется в корзине до оформления, и режим «только один сервер»: после успешного резерва новые резервы приостанавливаются на 24 часа.
- **Уведомления** (по умолчанию включены все): сервер появился, сервер снова раскупили, резервы и заказы, ошибки API, запуск и остановка, итоги дня. Ключ OVH проверяется при запуске, поэтому ключ без прав на работу с корзинами сразу заметен.
- **Щадящая нагрузка на API:** один небольшой запрос на тариф за проверку, увеличивающаяся пауза при ответах HTTP 429 и 5xx (до 5 минут), не более 3 попыток заказа на одно появление сервера.
- **Защищённый контейнер:** файловая система только для чтения, все capabilities отключены, запуск не от root, ограничения по памяти, CPU и числу процессов.

### Тарифы и локации

| Регион | Дата-центры |
|--------|-------------|
| Европа | GRA, RBX, SBG, PAR и зоны `eu-west-par-*`, FRA, WAW, LON, ERI, LIM |
| Северная Америка | BHS, VIN, HIL, зоны `ca-*` и `us-*` |
| Азиатско-Тихоокеанский регион | SGP, SYD, YNM |

Не каждый тариф продаётся во всех дата-центрах. Например, сейчас KS-B и KS-C доступны только в GRA, KS-3 в BHS, FRA, GRA, LON, RBX, SBG и WAW, а тарифы с окончанием `-sgp` и `-syd` только в своём дата-центре. Тестовый прогон показывает, где продаётся каждый тариф. Если тариф есть в данных о наличии, но отсутствует в каталоге вашего аккаунта, заказать его нельзя, и прогон об этом сообщит.

Цены зависят от того, в какой стране зарегистрирован аккаунт OVH (параметр `OVH_SUBSIDIARY`). Аккаунты `IE`, `FR`, `DE` и других европейских стран работают через `ovh-eu` и оплачивают счета в евро, аккаунты `WE` и `WS` работают через `ovh-ca` и оплачивают в долларах. Укажите `OVH_ENDPOINT` и `OVH_SUBSIDIARY` того аккаунта, для которого созданы ключи.

### Как часто можно проверять наличие

В документации OVH лимит для `GET /v1/dedicated/server/datacenter/availabilities` не указан, поэтому его замерили:

| Тест | Результат |
|------|-----------|
| 1, 2 и 4 запроса в секунду в течение 30 секунд, с фильтром `planCode` | все ответы HTTP 200 примерно за 0,1 с, ни одного 429, заголовков с лимитами нет |
| Полный список без фильтра | 4,3 МБ на запрос (9017 строк), около 74 ГБ в сутки при проверке раз в 5 секунд |
| 25 полных выгрузок за 5 минут | ни один статус не изменился: данные обновляются раз в несколько минут |
| Заголовки ответа | `cache-control: no-cache, no-store` |

Поэтому бот всегда запрашивает данные по конкретному тарифу (около 216 байт на запрос). **Стандартный и рекомендуемый интервал: 5 секунд.** В панели можно выбрать от 2 до 60 секунд, рядом показано, сколько запросов в минуту это даст для текущего числа тарифов. Держите это число заметно ниже 240: это нагрузка, на которой проводился тест. Проверять чаще почти не имеет смысла, потому что сами данные обновляются медленнее.

### Требования

- Docker с Compose или Python 3.12
- Токен бота от [@BotFather](https://t.me/BotFather)
- Для резерва и покупки: ключи API OVH, созданные на [eu.api.ovh.com/createToken](https://eu.api.ovh.com/createToken/) (для аккаунтов в долларах и канадских аккаунтов на [ca.api.ovh.com/createToken](https://ca.api.ovh.com/createToken/)), с правами `GET` и `POST` на `/order/cart` и `/order/cart/*`, `DELETE` на `/order/cart/*` и `GET` на `/me`

### Установка

```bash
cp .env.example .env
# укажите TELEGRAM_BOT_TOKEN, OWNER_ID (или OWNER_USERNAME) и ключи OVH
docker compose up -d --build
docker compose logs -f
```

Откройте бота в Telegram и отправьте `/start`. Если задан только `OWNER_USERNAME`, первая команда `/start` от этого пользователя в личном чате навсегда закрепит его числовой id, после чего имя пользователя больше не учитывается.

### Тестовый прогон из командной строки

```bash
docker compose exec sniper python main.py --dry-run
docker compose exec sniper python main.py --dry-run 24sk302@bhs 24sk302-sgp@sgp 24skgame012
```

Без аргументов проверяются тарифы, за которыми следит бот. В аргументах указываются коды тарифов, при необходимости с дата-центром через `@`. Команда завершается с кодом 0, только если проверка всех тарифов прошла успешно.

### Настройки

| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `TELEGRAM_BOT_TOKEN` | | Токен бота |
| `OWNER_ID` | | Числовой Telegram id единственного пользователя бота |
| `OWNER_USERNAME` | | Используется один раз, чтобы закрепить `OWNER_ID`, если он не задан |
| `OVH_APP_KEY`, `OVH_APP_SECRET`, `OVH_CONSUMER_KEY` | | Ключи API OVH; без них бот только присылает уведомления |
| `OVH_ENDPOINT` | `ovh-eu` | `ovh-eu`, `ovh-ca` или `ovh-us` |
| `OVH_SUBSIDIARY` | `IE` | Страна аккаунта OVH для корзины и каталога; `WE` и `WS` на `ovh-ca` работают в долларах |
| `TARGET_PLANS` | `25skb012:KS-B,25skc012:KS-C` | Тарифы в формате `код:название`; другие можно добавить в панели |
| `DEFAULT_REGION` | `eu` | Регион при первом запуске: `eu`, `na`, `apac` или `all` |
| `DEFAULT_MODE` | `reserve` | Режим при первом запуске |
| `POLL_INTERVAL` | `5` | Интервал проверки при первом запуске, в секундах |
| `MAX_PRICE` | `15.0` | Лимит цены при первом запуске, без налогов, в валюте аккаунта |
| `PRICE_CURRENCY` | `EUR` | Валюта, которая показывается рядом с ценами |
| `RESERVE_COOLDOWN_HOURS` | `24` | Пауза после успешного резерва, в часах |
| `ALLOW_AUTOPAY` | `false` | Разрешает режим покупки |
| `NICE` | `0` | Приоритет процесса (nice), от 0 до 19 |
| `STATE_PATH` | `data/state.json` | Файл, где хранятся настройки панели |

Значения «при первом запуске» только заполняют `state.json`. Дальше действуют настройки, заданные в панели.

### Тесты

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

### Лицензия

MIT, см. [LICENSE](LICENSE).
