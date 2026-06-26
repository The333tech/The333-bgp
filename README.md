# The333-BGP

**Локальный BGP-портал для управляемой публикации маршрутов на MikroTik.**

The333-BGP поднимает GoBGP speaker на Linux VM, собирает маршруты из источников и модулей сервисов, дедуплицирует/агрегирует их и публикует в MikroTik через BGP. Управление идёт через веб-портал: источники маршрутов, модули сервисов, Community-профили, диагностика, история, резервные копии, обновления и готовые команды для MikroTik.

[![Version](https://img.shields.io/badge/version-v0.1_stable-c88616)](VERSION)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/runtime-Docker-2496ed)](docs/INSTALL.md)
[![MikroTik](https://img.shields.io/badge/router-MikroTik-c88616)](docs/INSTALL.md)

## Быстрый старт

Команда рассчитана на чистую Linux VM. Установщик проверит Docker, определит IP VM, спросит BGP-параметры, попросит пароль портала и создаст `.env`.

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

Пароль задаётся при установке. Минимум — **8 символов**. Если оставить поле пустым, установщик сгенерирует пароль автоматически.

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

## License

MIT. См. [LICENSE](LICENSE).
