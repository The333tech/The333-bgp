# Установка The333-BGP

Версия: **v0.82.1b (beta)**

## 1. Что делает The333-BGP

The333-BGP публикует выбранные IPv4-маршруты через BGP на MikroTik. Управление идёт через веб-портал:

- источники готовых списков маршрутов;
- модули сервисов по конкретным сервисам;
- ручные записи;
- Community-профили;
- история обновлений и событий;
- диагностика BGP;
- резервные копии;
- обновления проекта.

## 2. Архитектура

```text
MikroTik  <---BGP--->  VM с The333-BGP
                         |
                         +-- the333-gobgp-core     : GoBGP speaker
                         +-- the333-bgp-backend    : API и расчёт маршрутов
                         +-- the333-portal         : веб-портал
                         +-- systemd updater       : host-side update через Unix socket
```

Порты по умолчанию:

```text
179/tcp   BGP
8088/tcp  backend
8090/tcp  portal
```

## 3. Быстрая установка

```bash
curl -fsSL https://raw.githubusercontent.com/The333tech/The333-bgp/main/install.sh | bash
```

Скрипт читает интерактивные ответы напрямую из терминала, поэтому команда остаётся интерактивной даже при запуске через pipe.

## 4. Что спросит установщик

Установщик интерактивный и проверяет типовые ошибки.

| Параметр | Что значит |
|---|---|
| IP VM | IP, на котором портал и BGP будут доступны в сети |
| ASN сервиса | Local AS The333-BGP; для локального стенда используй private ASN `64512`–`65534` |
| Router ID | Обычно IP VM |
| BGP nexthop | Обычно IP VM |
| IP MikroTik/BGP peer | IP MikroTik |
| ASN MikroTik/BGP peer | ASN MikroTik |
| BGP community по умолчанию | Метка маршрутов по умолчанию |
| Пароль портала | На странице входа вводится только пароль; минимум 8 символов; можно оставить пустым |

Если пароль оставить пустым, установщик сгенерирует его автоматически и **покажет один раз**. Сохрани значение сразу: в `.env` записывается только хеш, восстановить исходный пароль из него нельзя. После входа портал использует ограниченную по времени `HttpOnly`-сессию и CSRF-защиту; пароль не сохраняется в Web Storage и не отправляется с каждым API-запросом.

## 5. Автоматическое определение IP

Скрипт определяет IP VM командой:

```bash
ip route get 1.1.1.1
```

Это берёт IP интерфейса, через который VM выходит в сеть. Значение можно заменить вручную во время установки.

## 6. Проверки перед запуском

Установщик проверяет:

- валидность IPv4;
- валидность ASN;
- свободное место на диске: минимум 8 ГБ, рекомендуется 12+ ГБ;
- наличие Docker и Docker Compose plugin;
- наличие systemd для изолированного host-side updater;
- доступ текущего пользователя к Docker daemon или возможность выполнить Docker через `sudo`;
- занятость портов `179`, `8088`, `8090`;
- существующую установку в `/opt/the333-bgp`;
- наличие старого `.env`.

Если Docker уже установлен, установщик не переустанавливает его. Если daemon доступен только через `sudo`, текущая установка продолжится через `sudo` с явным сообщением.

Если проект уже установлен, скрипт предложит:

```text
update / backup / repair / status / quit
```

Режим `repair` заменяет файлы приложения, но сохраняет `.env`, `data/` и существующие пользовательские файлы `config/`.

## 7. Файлы проекта

По умолчанию проект ставится в:

```text
/opt/the333-bgp
```

Основные директории:

```text
app/       backend
portal/    frontend
config/    поставляемые конфиги
data/      пользовательское состояние и runtime-данные
scripts/   служебные команды
docker/    раздельные Dockerfile и entrypoint
deploy/    шаблон systemd service
backups/   резервные копии обновлятора
```

В поставке уже есть стартовые данные:

- `config/service_catalog.builtin.json` — встроенный каталог модулей сервисов текущей версии;
- `data/service_catalog.user.json` — добавленные пользователем модули, сохраняемые при обновлении;
- `config/service_candidates.seed.json` — найденные сервисы из Geosite;
- `config/default_sources.json` — стартовые источники маршрутов.

## 8. Портал

После установки открой:

```text
http://IP_VM:8090
```

Страница входа запрашивает только пароль — тот, который был задан или сгенерирован установщиком. Внутренняя учётная запись `admin` фиксирована и отдельного поля логина в интерфейсе нет.

## 9. MikroTik

В портале есть вкладка **Для MikroTik** с пошаговым помощником.

Порядок работы:

1. Скопируй из портала только read-only preflight-команды и выполни их в RouterOS Terminal.
2. Вставь вывод в помощник. Не вставляй sensitive export, ключи, пароли и содержимое AWG-конфигурации.
3. Проверь определённые RouterOS version, model, architecture, package `container` и device-mode.
4. Выбери доступный сценарий и проверь IP/ASN/Community.
5. Создай зашифрованный backup RouterOS, скачай его с роутера и проверь резервный доступ.
6. Выполни этап подготовки: BGP connection создаётся выключенным под quarantine filter.
7. Выполни read-only проверку созданных объектов.
8. Только после этого отдельно активируй BGP.

RouterOS 6 не поддерживает Containers и не поддерживается помощником The333-BGP. Для автоматизированной настройки проекта нужен RouterOS v7; не применяй сгенерированные RouterOS 7 команды к RouterOS 6.

Помощник автоматически выбирает совместимый BGP-синтаксис: connection-based профиль для RouterOS 7.0–7.19 и explicit instance для RouterOS 7.20+. До распознавания версии команды изменения конфигурации не генерируются.

Для peer в одной LAN MikroTik настраивается как direct eBGP с `multihop=no`. Переменная `BGP_DOCKER_BRIDGE_HOPS=1` учитывает единственный внутренний переход из GoBGP-контейнера через Docker bridge до LAN и автоматически задаёт минимальный транспортный TTL `2` только внутри core. Не увеличивай её без дополнительного routed hop и не включай `multihop=yes` на MikroTik для обычной установки в одной подсети.

GTSM/TTL security по умолчанию выключена для совместимости с уже настроенными peers. Включай её только одновременно на The333-BGP и MikroTik: одностороннее включение не даст BGP-сессии перейти в `Established`. Интерактивный установщик явно спрашивает про GTSM, а non-interactive установка включает её только при `BGP_TTL_SECURITY_ENABLED=true`.

Базовая логика:

```text
remote.address = IP VM
remote.as      = ASN сервиса
local.as       = ASN MikroTik
```

После настройки BGP MikroTik должен получить маршруты от The333-BGP.

> **Важно:** full-container AmneziaWG — отдельный сценарий. Не заменяй его стандартным WireGuard RouterOS. Используй только профиль, который прошёл проверку совместимости для твоей RouterOS и architecture.

## 10. Управление на VM

```bash
cd /opt/the333-bgp

./scripts/the333bgp.sh status
./scripts/the333bgp.sh backup
./scripts/the333bgp.sh check-update
./scripts/the333bgp.sh update
./scripts/the333bgp.sh set-password
./scripts/the333bgp.sh tls-enable /путь/к/portal.crt /путь/к/portal.key
./scripts/the333bgp.sh tls-disable
```

## 11. Проверка состояния

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh status
```

Ожидаемый смысл результата:

```text
the333-gobgp-core      healthy
the333-bgp-backend     healthy
the333-portal          up
the333-bgp-updater.service active
ready=true
gobgp_ready=true
Runtime health: backend, GoBGP and portal are ready.
```

## 12. Обновления

Обновления доступны в портале на странице **Обновления**.

Портал передаёт запрос host-side updater через защищённый Unix socket. Docker socket не монтируется в backend или другие контейнеры.

Процесс обновления:

1. проверяет доступные версии;
2. показывает changelog;
3. кратко приостанавливает только Backend, создаёт согласованный backup и запускает Backend снова; GoBGP остаётся онлайн;
4. загружает новую версию;
5. сохраняет пользовательские данные;
6. пересобирает контейнеры;
7. ждёт готовность backend, GoBGP и портала до 6 минут;
8. автоматически возвращает предыдущие файлы, если новая версия не прошла проверку готовности.

Обычное обновление портала/backend не пересоздаёт `the333-gobgp-core`, поэтому BGP-сессия и уже опубликованные маршруты продолжают работать. Во время снимка API и портал могут кратко переподключиться к Backend. Routing-core пересоздаётся только при изменении его отдельной версии или BGP-конфигурации; в таком случае MikroTik временно получит маршруты заново после восстановления сессии.

Ручное обновление на VM:

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh update
```

Обновление существующей установки через installer, например при переходе со старого релиза:

```bash
THE333_PROJECT_DIR=/opt/the333-bgp ./install.sh --action update
```

Проверка доступной версии:

```bash
./scripts/the333bgp.sh check-update
```

Выбор канала:

```bash
./scripts/the333bgp.sh check-update --channel stable
./scripts/the333bgp.sh check-update --channel beta
```

### Переход с ранних установок

Если установка была сделана до появления host-side updater, на VM может ещё работать старый контейнер `the333-host-updater` с Docker socket. Это legacy-режим; текущий установщик переносит обновление в изолированный systemd-сервис.

Проверка:

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh status
```

Если status пишет `Legacy updater container detected`, обнови установку новым installer:

```bash
curl -fsSL https://raw.githubusercontent.com/The333tech/The333-bgp/main/install.sh -o /tmp/the333-install.sh
bash /tmp/the333-install.sh --action update
```

Команда может запросить `sudo`-пароль VM. Это ожидаемо: systemd unit устанавливается в `/etc/systemd/system`, а runtime socket создаётся в `/run/the333-bgp`.

Если проект уже обновлён, но нужно отдельно переустановить только updater service:

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh install-updater-service
```

После успешного перехода ожидается:

```text
the333-bgp-updater.service active
/run/the333-bgp/updater.sock exists
```

Старый updater-container удаляется штатно при следующем `docker compose up -d --remove-orphans` после обновления файлов проекта.

## 13. Что сохраняется при обновлении

Сохраняются:

- `.env`;
- `data/`;
- пользовательские источники;
- состояние модулей сервисов;
- Community-профили;
- DNS cache;
- история обновлений;
- last-good маршруты.

### Выбор Community-профиля на MikroTik

На странице **Комьюнити** выбери профиль и укажи точное имя BGP connection из `/routing/bgp/connection/print`. Портал сформирует RouterOS v7 команды, которые:

1. создают отдельную цепочку `the333-bgp-profile-in`;
2. принимают только маршруты с выбранным `bgp-large-communities`;
3. назначают цепочку в `Routing → BGP → Connections → <connection> → Input Filter`;
4. перезапускают только выбранную BGP-сессию и выводят проверки;
5. содержат rollback к базовой цепочке `the333-bgp-in`.

В выводе `/routing/bgp/session/print detail` проверь `state=established` и `prefix-count`: это фактическое число маршрутов, принятых выбранным профилем. Команда `/ip/route ... routing-protocol=bgp` для этой проверки не используется, потому что на RouterOS v7 она может вернуть `0` даже при установленной сессии.

Выполняй этот блок после RouterOS backup и только в Safe Mode. Перезапуск BGP-сессии кратковременно убирает полученные маршруты до повторного установления соединения.

Справка MikroTik: [Route Selection and Filters](https://help.mikrotik.com/docs/spaces/ROS/pages/74678285/Route%20Selection%20and%20Filters) и [`/routing/bgp`](https://help.mikrotik.com/docs/spaces/ROS/pages/331612228/routing%2Bbgp).

## 14. Backup и восстановление

В портале есть кнопка **Бэкап**. Через неё можно создать backup, скачать архив, удалить лишний архив или восстановиться. Автобэкап настраивается там же: интервал задаётся в днях, количество хранимых архивов — от 1 до 100. По расписанию новый архив создаётся только тогда, когда `data/` или пользовательская часть `config/` действительно изменились.

Автообновление маршрутов настраивается из сайдбара или на странице **Маршруты**. Интервал задаётся в минутах (минимум 5); изменение применяется без редактирования `.env` и без перезапуска контейнеров. Настройки сохраняются в:

```text
/opt/the333-bgp/data/runtime_settings.json
```

Значения `AUTO_UPDATE`, `UPDATE_INTERVAL_SECONDS`, `SYSTEM_BACKUP_RETENTION`, `SYSTEM_BACKUP_AUTO_ENABLED` и `SYSTEM_BACKUP_AUTO_INTERVAL_DAYS` из `.env` используются как начальные только при создании этого файла.

CLI backup:

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh backup
```

Архивы обновлятора хранятся в:

```text
/opt/the333-bgp/backups
```

Системные backup-архивы портала хранятся в:

```text
/opt/the333-bgp/data/system_backups
```

## 15. Смена пароля

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh set-password
```

Команда создаёт backup `.env`, записывает новый hash пароля и перезапускает только backend.

## 16. HTTPS для портала

По умолчанию портал слушает локальный HTTP на `8090`. Для HTTPS подготовь PEM certificate chain и незашифрованный PEM private key:

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh tls-enable /путь/к/portal.crt /путь/к/portal.key
```

Команда:

1. проверит формат сертификата и ключа;
2. проверит, что public key совпадает;
3. сохранит private key вне проекта в root-owned `/etc/the333-bgp/tls` с чтением только для service-группы портала;
4. создаст backup `.env`;
5. проверит Compose и `nginx -t` до переключения;
6. включит `Secure` session cookie и откроет `https://IP_VM:8090`.

Отключение:

```bash
./scripts/the333bgp.sh tls-disable
```

Самоподписанный сертификат шифрует трафик, но браузер будет показывать предупреждение, пока его CA не добавлен в доверенные. Для постоянного доступа используй сертификат от доверенного внутреннего CA или reverse proxy с TLS.

## 17. Диагностика

Проверка контейнеров:

```bash
cd /opt/the333-bgp
docker compose -f docker-compose.yml -f docker-compose.portal.yml ps
```

Логи:

```bash
docker logs the333-bgp-backend --tail 100
docker logs the333-gobgp-core --tail 100
docker logs the333-portal --tail 100
sudo journalctl -u the333-bgp-updater.service -n 100 --no-pager
```

## 18. Безопасность

The333-BGP рассчитан на локальную сеть рядом с MikroTik.

**Не открывай `8090`, `8088` и `179` напрямую в Интернет.**

Для удалённого доступа используй VPN, firewall allowlist или reverse proxy с TLS.

Перед изменением BGP на MikroTik обеспечь резервный способ доступа к роутеру. Ошибка в ASN, peer address или политике маршрутизации может нарушить доступ к сети.
