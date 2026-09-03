# MCPE Server Bot — GenisysPro / LiteCore 1.1.5 + Docker + Telegram (2026)

Телеграм-бот на **Python + pyTelegramBotAPI (telebot)**, который одной кнопкой
**«🚀 Создать сервер»** поднимает настоящий сервер Minecraft PE на ядре
**GenisysPro (LiteCore 1.1.5, MCPE 1.1.5)** в Docker, сам открывает UDP-порт на
роутере и выдаёт **реальный временный рабочий адрес**, который можно сразу
вставить в игру.

```
Пользователь → [🚀 Создать сервер] → бот сам:
  1. берёт свободный UDP-порт из пула (19132…19160)
  2. создаёт контейнер GenisysPro + постоянный volume под миры
  3. открывает порт на роутере: UPnP → NAT-PMP → playit.gg → прямой IP
  4. проверяет внешний IP и присылает адрес вида 203.0.113.7:19132
  5. продлевает аренду порта каждую минуту, а по TTL — гасит сервер и
     закрывает порт (адрес именно временный)
```

---

## Быстрый старт (3 шага)

```bash
unzip mcpe-bot.zip && cd mcpe-bot

# всё автоматически: Docker, .env, сборка образов, фаервол, запуск
bash scripts/install.sh
```

Скрипт спросит только токен от **@BotFather** и твой Telegram ID
(его можно узнать командой `/id` у самого бота).

Если предпочитаешь вручную:

```bash
cp .env.example .env && nano .env      # вписать BOT_TOKEN и ADMIN_IDS
make build                             # образ PHP7+GenisysPro и образ бота
make up                                # запуск бота
make logs                              # смотреть логи
```

Потом в Telegram: `/start` → **🚀 Создать сервер**.

---

## Что внутри

```
mcpe-bot/
├─ bot/                     # Telegram-бот (Python 3.13)
│  ├─ bot.py                # хендлеры, кнопки, фоновая служба TTL/аренды
│  ├─ provisioner.py        # Docker: create/start/stop/restart/delete/logs/console
│  ├─ publisher.py          # выдача внешнего адреса (UPnP/NAT-PMP/playit/direct)
│  ├─ upnp.py               # свой UPnP IGD-клиент (SSDP + SOAP), без зависимостей
│  ├─ natpmp.py             # свой NAT-PMP клиент (RFC 6886)
│  ├─ netinfo.py            # внешний IP, свободен ли UDP-порт, minecraft:// ссылка
│  ├─ db.py                 # SQLite (серверы, порты, адреса, TTL)
│  ├─ config.py             # вся конфигурация из .env
│  ├─ requirements.txt      # pyTelegramBotAPI 4.28+, docker SDK 7.1+
│  └─ Dockerfile            # python:3.13-slim + tini + healthcheck
├─ server-image/
│  ├─ Dockerfile            # PHP 7 (pthreads) из pmmp/PHP-Binaries + GenisysPro.phar
│  ├─ entrypoint.sh         # генерит server.properties/pocketmine.yml, ops, старт
│  └─ GenisysPro.phar       # твоё ядро (уже внутри архива)
├─ scripts/
│  ├─ install.sh            # установка «в один клик»
│  └─ firewall.sh           # ufw/firewalld/nftables/iptables — открыть UDP-пул
├─ tests/selftest.py        # оффлайн-тесты (без Docker, Telegram и сети)
├─ docker-compose.yml       # bot + сборка образа + ручной сервер + playit
├─ Makefile                 # make install/build/up/down/logs/ps/test/firewall
└─ .env.example             # все настройки с комментариями
```

---

## Кнопки бота

| Кнопка | Что делает |
|---|---|
| 🚀 Создать сервер | порт + контейнер + проброс порта + адрес (одна кнопка, как просил) |
| ▶️ Запустить / ⏹ Остановить | старт/стоп; при стопе порт на роутере закрывается |
| 🔄 Рестарт | корректный перезапуск (сначала `stop` в консоль — миры сохраняются) |
| 📊 Статус | состояние, CPU, RAM, сколько осталось до автостопа |
| 📜 Логи | последние 40 строк консоли сервера |
| 🖥 Консоль | отправить любую команду: `op Steve`, `gamemode 1 Steve`, `save-all`, `tps` |
| 🌐 Обновить адрес | заново пробросить порт и получить свежий адрес |
| 🗑 Удалить | контейнер + volume + закрытие порта (с подтверждением) |

Команды: `/start`, `/servers`, `/diag` (проверка Docker и образа), `/id`, `/help`.

---

## Как получается «настоящий временный адрес»

`ADDRESS_MODE=auto` пробует по порядку:

1. **UPnP** — бот сам находит роутер (SSDP), просит `AddPortMapping` на UDP-порт
   с арендой `UPNP_LEASE_SECONDS=3600` и берёт реальный WAN-IP через
   `GetExternalIPAddress`. Аренда продлевается раз в минуту, поэтому если бот
   упадёт — роутер сам закроет порт.
2. **NAT-PMP** — то же самое для роутеров, где UPnP выключен, а NAT-PMP включён.
3. **playit.gg** — если указан `PLAYIT_SECRET`, бот поднимает контейнер
   playit-agent и выдаёт глобальный адрес вида `xxx.gl.at.ply.gg:12345`.
   Работает **даже за CGNAT** (мобильный интернет, серый IP).
4. **direct** — на VPS с публичным IP просто отдаётся `<публичный IP>:<порт>`,
   локальный фаервол при этом открывает `scripts/firewall.sh`.

Если ни один способ не сработал, бот честно скажет, что доступен только
локальный адрес, и подскажет причину (например «роутер за NAT провайдера»).

> Совет: серый IP у провайдера → включи playit.gg
> (`PLAYIT_SECRET`, туннель типа **Minecraft Bedrock** на `127.0.0.1:19132`).

---

## Ядро и версии (актуальные на 2026)

- **Бот:** Python 3.13-slim, pyTelegramBotAPI ≥ 4.28, docker SDK ≥ 7.1, tini,
  healthcheck, `restart: unless-stopped`.
- **Compose:** современный синтаксис v2 (`name:`, profiles, без устаревшего
  `version:`).
- **Сервер:** GenisysPro (форк PocketMine-MP эпохи Genisys/LiteCore, MCPE
  **1.1.5**) — ему нужен **PHP 7.x с pthreads**, поэтому образ многостадийный:
  первая стадия сама собирает PHP 7.2 (ZTS) из исходников php.net плюс
  расширения pthreads и yaml (`server-image/build-php.sh`), вторая — тонкий
  рантайм с `.phar`. `pmmp/PHP-Binaries` больше не используется: апстрим
  удалил ветки `php-7.x`, а его текущие скрипты собирают только PHP 8.
  Первая сборка занимает 10–25 минут; можно ускорить готовым бинарником:

  ```bash
  PHP_TARBALL_URL=https://.../PHP-7.2-Linux-x86_64.tar.gz make build
  ```

  Версии сборки меняются в `.env`: `PHP_VERSION`, `PTHREADS_REF`, `YAML_VERSION`.
  Если pthreads не собрался, сборка падает сразу — молча нерабочего
  образа не получишь.
- Игровые контейнеры по умолчанию идут в `network_mode: host` — для Bedrock
  (UDP/RakNet) это надёжнее, чем docker-proxy. Можно переключить на `bridge`
  через `SERVER_NETWORK_MODE`.

---

## Основные настройки `.env`

| Переменная | Смысл | По умолчанию |
|---|---|---|
| `BOT_TOKEN` | токен @BotFather | — |
| `ADMIN_IDS` | кому можно создавать серверы | пусто |
| `OPEN_FOR_EVERYONE` | `true` — доступ всем | `false` |
| `PORT_RANGE_START/END` | пул UDP-портов | `19132/19160` |
| `ADDRESS_MODE` | `auto/upnp/natpmp/playit/direct/manual` | `auto` |
| `UPNP_LEASE_SECONDS` | аренда проброса порта | `3600` |
| `SERVER_TTL_MINUTES` | автостоп сервера, `0` = бессрочно | `240` |
| `MAX_SERVERS_PER_USER` | лимит на пользователя | `2` |
| `SERVER_MEMORY_MB`, `SERVER_CPU_LIMIT`, `PHP_MEMORY_LIMIT_MB` | лимиты ресурсов | `1024`, `1.0`, `768` |
| `PLAYIT_SECRET` | ключ агента playit.gg | пусто |

---

## Проверка без Docker и сети

```bash
make test        # или: python3 tests/selftest.py
```

Тесты гоняют логику на «поддельных» Docker и Telegram: уникальность портов,
создание/старт/стоп/удаление, отправку команд в консоль, парсинг статистики,
UPnP-XML роутера, NAT-PMP обмен с локальным фейковым роутером, карточку
сервера и то, что в меню ровно одна кнопка создания.

---

## Частые проблемы

| Симптом | Решение |
|---|---|
| `⛔️ Доступ закрыт` | пришли `/id`, впиши число в `ADMIN_IDS`, `make restart` |
| `Образ … не собран` | `make build` |
| «Нет доступа к Docker» | проверь монтирование `/var/run/docker.sock` в `docker-compose.yml` |
| Адрес локальный (192.168.x.x) | UPnP выключен на роутере или серый IP → включи UPnP либо `PLAYIT_SECRET` |
| Сервер стартует и падает | `📜 Логи`; в конце сборки должен быть pthreads в `php -m` |
| `Remote branch php-7.2 not found` | старый Dockerfile: обнови проект, PHP теперь собирается из php.net (`server-image/build-php.sh`) |
| Игра не видит сервер | в игре «Добавить сервер» вручную, адрес + порт; проверь UDP, а не TCP |

---

## Токен и безопасность для GitHub

Токен уже прописан в `.env` (и туда же вписан твой Telegram ID), а сам код чистый —
его можно заливать на GitHub, push protection не сработает:

- `.env` в `.gitignore` вместе с `secrets/`, `data/`, `*.sqlite3`, `*.zip`, `*.key`.
  В отслеживаемых файлах нет ни одной строки, похожей на токен, — `.env.example`
  теперь с пустым `BOT_TOKEN=`.
- `make hooks` включает git-хук `pre-commit`: он ищет в изменениях шаблон
  `<6–12 цифр>:<35 символов>`, `.env`, `secrets/*`, базы и архивы — и отклоняет
  коммит, показывая файл с замаскированным значением.
- `make check-secrets` — та же проверка по всему дереву (её же гоняет `install.sh`).
- Можно вообще не держать токен в файлах проекта:

  ```bash
  mkdir -p secrets && printf '%s' '<токен>' > secrets/bot_token
  # в .env: BOT_TOKEN=   (пусто)
  #         BOT_TOKEN_FILE=/run/secrets/bot_token
  ```

  Каталог `secrets/` монтируется в контейнер только для чтения и игнорируется git.
- Токен ты присылал в переписке, поэтому по-хорошему стоит его перевыпустить:
  `/revoke` у @BotFather → новый токен в `.env` → `make restart`. Больше ничего
  менять не нужно.
- Права на файл: `chmod 600 .env` (installer делает это сам).

## Безопасность

Боту смонтирован `docker.sock` — это фактически root на хосте, поэтому:
держи `OPEN_FOR_EVERYONE=false`, пускай только свои `ADMIN_IDS`, не публикуй
токен и `.env`. Игровые контейнеры ограничены по CPU/RAM и работают под
непривилегированным пользователем `mc` внутри образа.
