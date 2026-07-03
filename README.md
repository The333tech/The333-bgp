# The333-BGP

**Локальный BGP-портал для управляемой публикации маршрутов на MikroTik.**

The333-BGP поднимает GoBGP speaker на Linux VM, собирает маршруты из источников и модулей сервисов, дедуплицирует/агрегирует их и публикует в MikroTik через BGP. Управление идёт через веб-портал: источники маршрутов, модули сервисов, Community-профили, диагностика, история, резервные копии, обновления и пошаговый помощник MikroTik.

<div align="center">

[![Version](https://img.shields.io/badge/Version-v0.1_stable-c88616?style=flat-square)](VERSION)
[![License: MIT](https://img.shields.io/badge/License-MIT-f1c40f?style=flat-square)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ed?style=flat-square&logo=docker&logoColor=white)](docs/INSTALL.md)
[![MikroTik](https://img.shields.io/badge/MikroTik-RouterOS_v7-c88616?style=flat-square)](docs/INSTALL.md)
[![CI](https://img.shields.io/github/actions/workflow/status/The333tech/The333-bgp/ci.yml?branch=main&label=CI&style=flat-square)](https://github.com/The333tech/The333-bgp/actions/workflows/ci.yml)
[![CodeQL](https://img.shields.io/github/actions/workflow/status/The333tech/The333-bgp/codeql.yml?branch=main&label=CodeQL&style=flat-square)](https://github.com/The333tech/The333-bgp/actions/workflows/codeql.yml)

</div>

> [!NOTE]
> **v0.1 — первый публичный релиз.** Рабочий стенд на отдельной VM в локальной сети успешно публикует маршруты в MikroTik; локальные тесты и dependency audit проходят успешно, workflows CI и CodeQL включены в репозиторий. Это ранний выпуск, поэтому на других конфигурациях возможны ошибки. Перед окончательной публикацией релиза установка из GitHub дополнительно проверяется на новой чистой VM, а статусы CI и CodeQL должны быть зелёными для release commit.

## Требования

- Linux VM с Ubuntu/Debian или заранее установленными Docker и Docker Compose plugin;
- статический IPv4 для VM;
- MikroTik с RouterOS v7;
- минимум 2 ГБ RAM и 8 ГБ свободного места, рекомендуется 10+ ГБ свободного места;
- доступ VM к Интернету для загрузки образов и списков маршрутов.

## Быстрый старт

Команда рассчитана на чистую Linux VM. Установщик проверит Docker, автоматически определит IP VM, проверит сетевые параметры, запросит пароль портала и создаст защищённый `.env`.

```bash
curl -fsSL https://raw.githubusercontent.com/The333tech/The333-bgp/main/install.sh | bash
```

После установки:

```text
Portal:  http://IP_VM:8090
Backend: http://IP_VM:8088
BGP:     IP_VM:179
Login:   admin
```

Пароль задаётся при установке. Минимум — **8 символов**. Если оставить поле пустым, установщик сгенерирует и **один раз покажет** пароль; в `.env` сохраняется только его хеш.

## Что внутри

| Компонент | Назначение |
|---|---|
| `the333-gobgp-core` | GoBGP speaker и BGP-сессия с MikroTik |
| `the333-bgp-backend` | API, расчёт маршрутов, источники, модули, Community, jobs |
| `the333-host-updater` | локальный обновлятор проекта через GitHub и Docker Compose |
| `the333-portal` | веб-портал через nginx |
| `scripts/the333bgp.sh` | CLI-команда для status, backup, update и смены пароля |

## Возможности

- **Источники маршрутов**: готовые CIDR-списки, ручные записи и выбор нескольких источников одновременно.
- **Модули сервисов**: точечные модули для OpenAI, Anthropic/Claude, YouTube, Google, Telegram, X/Twitter и других сервисов.
- **Найденные сервисы**: seed-каталог из Geosite с кандидатами, которые можно добавлять в свой каталог.
- **Дедупликация и агрегация**: итоговый набор маршрутов собирается без лишних дублей.
- **Community-профили**: разные наборы маршрутов под разные BGP community.
- **История и диагностика**: последние события, ready/status, GoBGP output, сравнение наборов маршрутов.
- **Бэкапы**: создание, скачивание, удаление и восстановление через портал.
- **Обновления**: проверка новых версий и обновление проекта через портал.
- **Защита обновления**: pre-update backup, проверка готовности после перезапуска и автоматический rollback файлов при неудачном обновлении.
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
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

Ошибки и предложения: [GitHub Issues](https://github.com/The333tech/The333-bgp/issues).

## License

MIT. См. [LICENSE](LICENSE).
