# The333-BGP

**Локальный BGP-портал для управляемой публикации маршрутов на MikroTik.**

The333-BGP поднимает GoBGP speaker на Linux VM, собирает маршруты из источников и модулей сервисов, дедуплицирует/агрегирует их и публикует в MikroTik через BGP. Управление идёт через веб-портал: источники маршрутов, модули сервисов, Community-профили, диагностика, история, резервные копии, обновления и пошаговый помощник MikroTik.

<p align="center"><a href="VERSION"><img alt="Version v0.82.4b beta, MIT" src="https://img.shields.io/badge/Version-v0.82.4b%20beta%20%7C%20MIT-8e44ad?style=flat-square"></a><a href="docs/INSTALL.md"><img alt="Docker, RouterOS v7, GoBGP 4.7.0" src="https://img.shields.io/badge/Runtime-Docker%20%7C%20RouterOS%20v7%20%7C%20GoBGP%204.7.0-00a6a6?style=flat-square"></a><a href="https://github.com/The333tech/The333-bgp/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/The333tech/The333-bgp/actions/workflows/ci.yml/badge.svg?branch=main"></a></p>

> [!NOTE]
> **v0.82.4b (beta)** означает, что проект ещё находится в активной разработке перед stable-релизом. Текущий рабочий стенд на отдельной VM в локальной сети успешно публикует маршруты в MikroTik больше месяца; тесты, документация и сценарии установки продолжают дорабатываться.

## Требования

- Linux VM с Ubuntu/Debian, systemd и заранее установленными Docker и Docker Compose plugin либо возможностью установить их;
- статический IPv4 для VM;
- MikroTik с RouterOS v7;
- минимум 1 ГБ RAM; рекомендуется 2+ ГБ;
- для новой установки: 4 ГБ свободного места при готовом Docker или 5 ГБ, если установщик должен поставить Docker; рекомендуется 6–8 ГБ;
- доступ VM к Интернету для загрузки образов и списков маршрутов.

### Почему нужен запас на диске

Сам проект лёгкий: для `v0.82.4b` release archive занимает около **350 КБ**, а исходный код и встроенные конфигурации после распаковки — около **3 МБ**. На тестовом стенде каталог проекта вместе с текущими данными и резервными копиями занимает около **100 МБ**.

Основное место требуется Docker и процессу обновления:

| Что занимает место | Ориентир для `v0.82.4b` |
|---|---:|
| Рабочие Docker images | около 1 ГБ |
| Docker build cache | около 0,5 ГБ |
| Проект, данные и резервные копии | около 0,1 ГБ |
| Обычный объём после установки | примерно 1,5–2 ГБ |

Во время установки и обновления локально собираются GoBGP, backend и portal. Docker временно хранит builder layers, старые и новые images, а The333-BGP сохраняет backup для rollback. Поэтому пиковое потребление заметно выше постоянного.

Установщик учитывает этап и состояние VM:

- **4 ГБ свободно** — жёсткий минимум для новой установки, если Docker и Docker Compose plugin уже готовы;
- **5 ГБ свободно** — жёсткий минимум, если Docker ещё предстоит установить;
- **1 ГБ свободно** — минимум для обычного update, который переиспользует установленный routing-core;
- **2 ГБ свободно** — минимум для update, если образ routing-core отсутствует и требуется его полная сборка;
- **3 ГБ свободно** — минимум для repair с повторной сборкой;
- **6–8 ГБ свободно** рекомендуется для спокойной эксплуатации, обновлений ОС, роста Docker cache, истории данных и нескольких циклов обновления без срочной очистки.

Проверка выполняется повторно перед Docker build. Если каталог проекта и Docker Root Dir находятся на разных разделах, свободное место проверяется отдельно на каждом из них. Установщик не запускает глобальный `docker system prune`, поэтому не удаляет images и cache других проектов на той же VM.

## Быстрый старт

Команда рассчитана на чистую Linux VM. Установщик проверит Docker, автоматически определит IP VM, проверит сетевые параметры, запросит пароль портала и создаст защищённый `.env`.

```bash
curl -fsSL https://raw.githubusercontent.com/The333tech/The333-bgp/main/install.sh | bash
```

Чтобы сначала просмотреть установщик, скачай его отдельно:

```bash
curl -fsSL https://raw.githubusercontent.com/The333tech/The333-bgp/main/install.sh -o /tmp/the333-install.sh
less /tmp/the333-install.sh
bash /tmp/the333-install.sh
```

После установки:

```text
Portal:  http://IP_VM:8090
Backend: http://IP_VM:8088
BGP:     IP_VM:179
```

Страница входа запрашивает только пароль. Он задаётся при установке; минимум — **8 символов**. Если оставить поле пустым, установщик сгенерирует и **один раз покажет** пароль; в `.env` сохраняется только его хеш. После входа портал использует ограниченную по времени `HttpOnly`-сессию и CSRF-защиту, не сохраняя пароль в браузере.

## Что внутри

| Компонент | Назначение |
|---|---|
| `the333-gobgp-core` | GoBGP speaker и BGP-сессия с MikroTik |
| `the333-bgp-backend` | API, расчёт маршрутов, источники, модули, Community, jobs |
| `the333-bgp-updater.service` | изолированный host-side обновлятор через systemd и Unix socket |
| `the333-portal` | веб-портал через nginx |
| `scripts/the333bgp.sh` | CLI-команда для status, backup, update и смены пароля |

## Возможности

- **Источники маршрутов**: готовые CIDR-списки, ручные записи и выбор нескольких источников одновременно.
- **Модули сервисов**: точечные модули для OpenAI, Anthropic/Claude, YouTube, Google, Telegram, X/Twitter и других сервисов.
- **Найденные сервисы**: seed-каталог из Geosite с кандидатами, которые можно добавлять в свой каталог.
- **Дедупликация и агрегация**: итоговый набор маршрутов собирается без лишних дублей.
- **Community-профили**: разные наборы маршрутов под разные BGP community.
- **История и диагностика**: последние события, ready/status, GoBGP output, сравнение наборов маршрутов.
- **Автообновление маршрутов**: включение, отключение и интервал в минутах настраиваются в портале без рестарта контейнеров.
- **Бэкапы**: создание, скачивание, удаление и восстановление через портал; автобэкап по расписанию создаёт новый архив только после реального изменения состояния.
- **Runtime**: портал показывает uptime Portal, Backend и GoBGP через ограниченный host-side API без Docker socket в контейнерах.
- **Обновления**: проверка новых версий и обновление проекта через портал.
- **Защита обновления**: verified release, согласованный pre-update backup с краткой паузой только Backend, durable-статус задачи, полная проверка готовности и автоматический rollback кода, `.env`, `config` и `data`; GoBGP продолжает публиковать маршруты.
- **Изоляция updater**: Docker socket не монтируется в контейнеры; backend обращается к узкому host-side API только через Unix socket и отдельный token.
- **BGP continuity**: версия routing-core отделена от версии портала, поэтому обычные UI/backend-обновления не перезапускают GoBGP без необходимости.
- **Direct eBGP через Docker**: MikroTik остаётся в штатном режиме `multihop=no`; единственный Docker bridge hop учитывается минимальным транспортным TTL внутри GoBGP и не превращает peer в routed multihop.
- **BGP transport security**: GTSM доступна как явная двухсторонняя настройка для direct eBGP; по умолчанию сохраняется совместимый режим. Опциональный TCP MD5 хранится в read-only secret-файле, а не в окружении контейнера.
- **Помощник MikroTik**: read-only preflight, проверка RouterOS/архитектуры/Containers и поэтапная BGP-настройка через карантинный фильтр, проверку и отдельную активацию.
- **Версионные профили RouterOS**: отдельная генерация BGP-команд для RouterOS 7.0–7.19 и 7.20+; до успешного preflight команды не выдаются.

## Управление на VM

```bash
cd /opt/the333-bgp

./scripts/the333bgp.sh status
./scripts/the333bgp.sh backup
./scripts/the333bgp.sh check-update
./scripts/the333bgp.sh update
./scripts/the333bgp.sh set-password
```

## Безопасность

The333-BGP рассчитан на запуск в доверенной локальной сети рядом с MikroTik.

**Не открывай `8090`, `8088` и `179` напрямую в Интернет.**

Для доступа извне используй VPN, firewall allowlist или reverse proxy с TLS.

Для HTTPS непосредственно на портале подготовь PEM-сертификат и незашифрованный private key, затем выполни:

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh tls-enable /путь/к/portal.crt /путь/к/portal.key
```

Команда проверяет пару certificate/key, хранит ключ вне каталога проекта в root-owned `/etc/the333-bgp/tls` с доступом только у service-группы портала, тестирует nginx и переключает портал на `https://IP_VM:8090`. Вернуться к локальному HTTP: `./scripts/the333bgp.sh tls-disable`.

Подробнее: [SECURITY.md](SECURITY.md)

> [!WARNING]
> Публикация BGP-маршрутов меняет маршрутизацию сети. Перед применением проверь ASN, IP peer/nexthop и результат preflight во вкладке **Для MikroTik**. Сначала создай зашифрованный backup и проверь резервный доступ через MAC WinBox или консоль. Не вставляй в портал private keys, пароли и sensitive export RouterOS.

## Восстановление пароля

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh set-password
```

Команда создаёт backup `.env`, записывает новый hash пароля и перезапускает только backend. GoBGP core при этом не перезапускается.

## Документация

- [Полная инструкция установки](docs/INSTALL.md)
- [Как внести вклад](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

Ошибки и предложения: [GitHub Issues](https://github.com/The333tech/The333-bgp/issues).

## License

MIT. См. [LICENSE](LICENSE).
