# The333-BGP

**Локальный BGP-портал для управляемой публикации маршрутов на MikroTik.**

The333-BGP поднимает GoBGP speaker на Linux VM, собирает маршруты из источников и модулей сервисов, дедуплицирует/агрегирует их и публикует в MikroTik через BGP. Управление идёт через веб-портал: источники маршрутов, модули сервисов, Community-профили, диагностика, история, резервные копии, обновления и пошаговый помощник MikroTik.

<p align="center"><kbd><a href="VERSION"><img alt="Version" src="https://img.shields.io/badge/Version-v0.94b-8e44ad?style=flat-square"></a></kbd>&nbsp;<kbd><a href="update-manifest.json"><img alt="Channel" src="https://img.shields.io/badge/Channel-beta-7f52ff?style=flat-square"></a></kbd>&nbsp;<kbd><a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/License-MIT-f1c40f?style=flat-square"></a></kbd>&nbsp;<kbd><a href=".github/workflows/release.yml"><img alt="SBOM" src="https://img.shields.io/badge/SBOM-SPDX-546e7a?style=flat-square"></a></kbd><br><kbd><a href="docs/INSTALL.md"><img alt="Docker" src="https://img.shields.io/badge/Docker-ready-2496ed?style=flat-square&logo=docker&logoColor=white"></a></kbd>&nbsp;<kbd><a href="docs/MIKROTIK_AWG.md"><img alt="MikroTik" src="https://img.shields.io/badge/MikroTik-RouterOS%20v7-c88616?style=flat-square"></a></kbd>&nbsp;<kbd><a href="docker/backend.Dockerfile"><img alt="Python" src="https://img.shields.io/badge/Python-3.14-3776ab?style=flat-square&logo=python&logoColor=white"></a></kbd>&nbsp;<kbd><a href="portal/package.json"><img alt="Node.js" src="https://img.shields.io/badge/Node.js-24-3c873a?style=flat-square&logo=node.js&logoColor=white"></a></kbd><br><kbd><a href="https://github.com/osrg/gobgp/releases/tag/v4.9.0"><img alt="GoBGP" src="https://img.shields.io/badge/GoBGP-v4.9.0-00a6a6?style=flat-square"></a></kbd>&nbsp;<kbd><a href="https://github.com/The333tech/The333-bgp/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/The333tech/The333-bgp/actions/workflows/ci.yml/badge.svg?branch=main"></a></kbd>&nbsp;<kbd><a href="https://github.com/The333tech/The333-bgp/actions/workflows/release.yml"><img alt="Release" src="https://github.com/The333tech/The333-bgp/actions/workflows/release.yml/badge.svg"></a></kbd>&nbsp;<kbd><a href="https://github.com/The333tech/The333-bgp/actions/workflows/codeql.yml"><img alt="CodeQL" src="https://github.com/The333tech/The333-bgp/actions/workflows/codeql.yml/badge.svg?branch=main"></a></kbd></p>

> [!NOTE]
> **v0.94b (beta)** означает, что проект ещё находится в активной разработке перед stable-релизом. Рабочий стенд на отдельной VM в локальной сети публикует маршруты в MikroTik; установка сторонним пользователем пока не подтверждена. При переходе с ревизии GoBGP Core старее `4.9.0-r2` запланируй короткое окно обслуживания с временным разрывом BGP-сессии; при обновлении с `v0.93b` Core не меняется. RouterOS, AWG-контейнер и VPN-профили обновление The333-BGP не меняет.

## Требования

- Linux VM с Ubuntu/Debian, systemd и заранее установленными Docker и Docker Compose plugin либо возможностью установить их;
- статический IPv4 для VM;
- MikroTik с RouterOS v7;
- минимум 1 ГБ RAM; рекомендуется 2+ ГБ;
- для готовых образов свободное место рассчитывается по недостающим слоям, данным и операции; общего обязательного порога 1–2 ГБ нет. Установка самого Docker учитывается отдельно;
- для резервной сборки из исходников: 4 ГБ при готовом Docker или 5 ГБ вместе с установкой Docker; рекомендуется 6–8 ГБ;
- доступ VM к Интернету для загрузки образов и списков маршрутов.

### Почему нужен запас на диске

Сам проект лёгкий: release archive занимает несколько сотен килобайт, а исходный код и встроенные конфигурации после распаковки — около **3 МБ**. На тестовом стенде каталог проекта вместе с текущими данными и резервными копиями занимает около **100 МБ**.

Основное место требуется Docker и процессу обновления:

| Компонент | Измеренный ориентир для готовых образов |
|---|---:|
| Загрузка всех трёх образов | около 90 МиБ, сжатые слои |
| Сумма распакованных слоёв образов | около 230 МиБ, до вычета общих слоёв |
| Исходный код и встроенные настройки | около 3 МиБ |
| Пользовательские данные и архивы | зависят от списков, истории и количества копий |

В быстром `prebuilt`-режиме GoBGP, backend и portal загружаются из GHCR по неизменяемым SHA-256 digest, поэтому builder layers на VM не создаются. Во время обновления Docker всё равно временно хранит старые и новые images, а The333-BGP сохраняет backup для rollback. Резервный `source`-режим дополнительно собирает все образы локально и требует больше места.

В `prebuilt`-режиме установщик и updater учитывают уже имеющиеся слои, распаковку новых образов, размер полной резервной копии и место для восстановления. После загрузки образов запас на их загрузку повторно не требуется. Смена версии GoBGP сама по себе не включает порог 2 ГБ.

Проверки выполняются по этапам, отдельно на дисках проекта, Docker/containerd и временных файлов. На каждом диске остаётся небольшой рабочий резерв; он не заменяет рассчитанный объём операции. Большие пользовательские данные или обновление базовых образов закономерно требуют больше места, чем небольшое исправление приложения. Точный расчёт и причина отказа выводятся в журнал, при нехватке места файлы рабочей версии не заменяются. **Резервное копирование и защита отката не отключаются.**

Для `source`-сборки сохраняется отдельный запас под компилятор и build cache: 4 ГБ для новой установки с готовым Docker, 5 ГБ с установкой Docker, 1–2 ГБ для обновления и 3 ГБ для repair. Это не требования быстрой установки готовых образов. Место под ОС, её обновления и другие сервисы в размер The333-BGP не входит.

Установщик не запускает глобальный `docker system prune` и не удаляет образы или cache чужих проектов. Подробности проверки и перехода со старого updater: [инструкция установки](docs/INSTALL.md#6-проверки-перед-запуском).

## Быстрый старт

Быстрый beta-режим загружает готовые `amd64/arm64` images из GHCR. Установщик проверит release metadata и SHA-256, определит архитектуру и IP VM, установит Docker при необходимости, запросит сетевые параметры и пароль портала и создаст защищённый `.env`.

```bash
curl -fsSL https://raw.githubusercontent.com/The333tech/The333-bgp/main/install.sh \
  | THE333_IMAGE_MODE=prebuilt bash
```

Для аудита и полностью локальной сборки образов из исходников сохранён резервный режим:

```bash
curl -fsSL https://raw.githubusercontent.com/The333tech/The333-bgp/main/install.sh | bash
```

Чтобы сначала просмотреть установщик, скачай его отдельно:

```bash
curl -fsSL https://raw.githubusercontent.com/The333tech/The333-bgp/main/install.sh -o /tmp/the333-install.sh
less /tmp/the333-install.sh
THE333_IMAGE_MODE=prebuilt bash /tmp/the333-install.sh
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
| `scripts/the333bgp.sh` | CLI-команда для status, backup, update, отчёта о месте и безопасной ротации |

## Возможности

- **Источники маршрутов**: готовые CIDR-списки, ручные записи и выбор нескольких источников одновременно.
- **Модули сервисов**: точечные модули для OpenAI, Anthropic/Claude, YouTube, Google, Telegram, X/Twitter и других сервисов.
- **Найденные сервисы**: seed-каталог из Geosite с кандидатами, которые можно добавлять в свой каталог.
- **Дедупликация и агрегация**: итоговый набор маршрутов собирается без лишних дублей.
- **Community-профили**: разные наборы маршрутов под разные BGP community.
- **История и диагностика**: последние события, ready/status, GoBGP output, сравнение наборов маршрутов.
- **Проверка адреса**: сохранённый префикс, наличие в локальном GoBGP RIB и запускаемый вручную TCP/443-тест из Backend. Приём на MikroTik и выход через VPN проверяются отдельно.
- **Автообновление маршрутов**: включение, отключение и интервал в минутах настраиваются в портале без рестарта контейнеров.
- **Бэкапы**: создание, скачивание, удаление и восстановление через портал; автобэкап по расписанию создаёт новый архив только после реального изменения состояния.
- **Runtime**: портал показывает uptime Portal, Backend и GoBGP через ограниченный host-side API без Docker socket в контейнерах.
- **Обновления**: проверка версий и отображение этапов обновления прямо на странице; после проверки готовности новой версии портал перезагружается.
- **Защита обновления**: проверенный релиз, согласованный backup, сохраняемое состояние этапов, блокировка изменений, проверка готовности и автоматический rollback кода, `.env`, `config` и `data`. Ошибка выделяется красным, таймер останавливается, а известные причины сопровождаются дальнейшими действиями. GoBGP остаётся онлайн только если версия и ревизия routing-core не меняются; переход на Core `4.9.0-r2` с более ранней ревизии временно прерывает BGP-сессию.
- **Изоляция updater**: Docker socket не монтируется в контейнеры; backend обращается к узкому host-side API только через Unix socket и отдельный token.
- **BGP continuity**: версия routing-core отделена от версии портала, поэтому обычные UI/backend-обновления не перезапускают GoBGP без необходимости.
- **Direct eBGP через Docker**: MikroTik остаётся в штатном режиме `multihop=no`; единственный Docker bridge hop учитывается минимальным транспортным TTL внутри GoBGP и не превращает peer в routed multihop.
- **BGP transport security**: GTSM доступна как явная двухсторонняя настройка для direct eBGP; по умолчанию сохраняется совместимый режим. Опциональный TCP MD5 хранится в read-only secret-файле, а не в окружении контейнера.
- **Помощник MikroTik**: read-only preflight, проверка RouterOS/архитектуры/Containers и поэтапная BGP-настройка через карантинный фильтр, проверку и отдельную активацию.
- **Версионные профили RouterOS**: отдельная генерация BGP-команд для RouterOS 7.0–7.19 и 7.20+; до успешного preflight команды не выдаются.

## Границы работы

- Публикуются только IPv4-маршруты. IPv6-трафик в dual-stack сети этим BGP-набором не управляется.
- Кандидаты из Geosite преобразуются в IP-адреса через DNS на VM. Правило `domain:` не перечисляет все поддомены, а общий IP нескольких сайтов нельзя разделить одним IP-маршрутом.
- Состояние GoBGP и результат проверки адреса в портале не подтверждают маршрут на MikroTik и фактический выход клиента через VPN. Проверяй их отдельно на роутере и с устройства в локальной сети; порядок проверок приведён в [инструкции MikroTik + AWG](docs/MIKROTIK_AWG.md).

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
- [MikroTik + AmneziaWG-контейнер](docs/MIKROTIK_AWG.md)
- [Как внести вклад](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

Ошибки и предложения: [GitHub Issues](https://github.com/The333tech/The333-bgp/issues).

## License

MIT. См. [LICENSE](LICENSE).
